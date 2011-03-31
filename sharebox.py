#!/usr/bin/env python
"""
Distributed mirroring filesystem based on git-annex.

Usage:

sharebox <mountpoint> [-o <option>]
sharebox -c [command] <mountpoint>

Options:
    -o gitdir=<path>            mandatory: path to the git directory to
                                actually store the files in.
    -o numversions=<number>     number of different versions of the same
                                file to keep. Any number <=0 means "keep
                                everything" (default 0).
    -o getall                   when there are modifications on a remote,
                                download the content of files.
    -o notifycmd                How the filesystem should notify you about
                                problems: string containing "%s" between
                                quotes (default:
                                'notify-send "sharebox" "%s"').
    -o foreground               debug mode.

Commands:
    sync                        queries all the remotes for changes and
                                merges if possible.
    merge                       the same except if there are conflicts,
                                a merge interface is spawned to help you
                                choose which files you want to keep
"""
from __future__ import with_statement

from errno import EACCES
import threading

import os
import os.path

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn

import shlex
import subprocess
import time
import sys
import getopt
import time

foreground = False

def ignored(path):
    """
    Returns true if we should ignore this file, false otherwise. This
    should respect the different ways for git to ignore a file.
    """
    path_ = path[2:]
    # Exception: files that are versionned by git but that we want to
    # ignore, and special sharebox directory.
    if (path_ == '.git-attributes' or
            path_.startswith('.git/') or
            path_.startswith('.git-annex/') or
            path_ == '.command'):
        return True
    else:
        ls_options = "-c -o -d -m --full-name --exclude-standard"
        considered = subprocess.Popen(
                shlex.split('git ls-files %s -- "%s"' % (ls_options, path_)),
                stdout=subprocess.PIPE).communicate()[0].strip().split('\n')
        return path_ not in considered

def annexed(path):
    """
    returns True if the file is annexed, false otherwise
    """
    return (os.path.islink(path) and
            os.readlink(path).count('.git/annex/objects'))

def shell_do(cmd):
    """
    calls the given shell command
    """
    if foreground:
        print cmd
    p = None
    stdin = None
    for i in cmd.split('|'):
        p = subprocess.Popen(shlex.split(i), stdin=stdin, stdout=subprocess.PIPE)
        stdin = p.stdout
    p.wait()
    return not p.returncode # will return True if everything ok

class AnnexUnlock:
    """
    Annex unlock operation

    Unlocks the given path before an operation and commits the result
    after.

    usage:
    >>>  with AnnexUnlock(path):
    >>>    dosomething()
    """
    def __init__(self, path):
        self.path = path
        self.annexed = annexed(path)

    def __enter__(self):
        if self.annexed:
            shell_do('git annex unlock "%s"' % self.path)

    def __exit__(self, type, value, traceback):
        if self.annexed:
            shell_do('git annex add "%s"' % self.path)
            shell_do('git commit -m "changed %s"' % self.path)

class CopyOnWrite:
    """
    Copy on Write operation

    Returns a suited file descriptor to use as a replacement for the one
    you provide. Can clean and commit when your operation is over.

    usage:

    >>>  with CopyOnWrite(path, fh, opened_copies, unlock=False,
    >>>         commit=False):
    >>>    dosomething()

    if opened_copies already contains a file descriptor opened for write
    to use as a replacement for fh, return it

    >>>  with CopyOnWrite(path, fh, opened_copies, unlock=True,
    >>>         commit=False):
    >>>    dosomething()

    same as above, except it will unlock a copy and create the file
    descriptor if it was not found in opened_copies

    >>>  with CopyOnWrite(path, fh, opened_copies, unlock=True,
    >>>         commit=True):
    >>>    dosomething()

    same as above, except after the operation the file descriptor in
    opened_copies will be closed and deleted, and the copy will be
    commited.
    """
    def __init__(self, path, fh, opened_copies, unlock, commit):
        self.path = path
        self.fh = fh
        self.opened_copies = opened_copies
        self.unlock = unlock
        self.commit = commit

    def __enter__(self):
        if self.unlock:
            if self.opened_copies.get(self.fh, None) == None:
                if annexed(self.path):
                    shell_do('git annex unlock "%s"' % self.path)
                    self.opened_copies[self.fh] = os.open(self.path,
                            os.O_WRONLY | os.O_CREAT)
        return self.opened_copies.get(self.fh, self.fh)

    def __exit__(self, type, value, traceback):
        if self.commit:
            if self.opened_copies.get(self.fh, None) != None:
                try:
                    os.close(self.opened_copies[self.fh])
                    del self.opened_copies[self.fh]
                except KeyError:
                    pass
            if not ignored(self.path):
                shell_do('git annex add "%s"' % self.path)
                shell_do('git commit -m "changed %s"' % self.path)

class ShareBox(LoggingMixIn, Operations):
    """
    Assumes operating from the root of the managed git directory

    git-annex allows to version only links to files and to keep their
    content out of git. Once a file is added to git annex, it is replaced
    by a symbolic link to the content of the file. The content of the file
    is made read-only by git-annex so that we don't modify it by mistake.

    What does this file system:
    - It automatically adds new files to git-annex.
    - It resolves git-annex symlinks so that we see them as regular
      writable files.
    - If the content of a file is not present on the file system, it is
      requested on the fly from one of the replicated copies.
    - When you access a file, it does copy on write: if you don't modify
      it, you read the git-annex copy. However, if you change it, the copy
      is unlocked on the fly and commited to git-annex when closed.
      Depending on the mount option, the previous copy can be kept in
      git-annex.
    - It pulls at regular intervals the other replicated copies and
      launches a merge program if there are conflicts.
    """
    def __init__(self, gitdir, mountpoint, numversions,
            getall):
        """
        Calls 'git init' and 'git annex init' on the storage directory if
        necessary.
        """
        self.gitdir = gitdir
        self.mountpoint = mountpoint
        self.numversions = numversions
        self.getall = getall
        self.rwlock = threading.Lock()
        self.opened_copies = {}
        with self.rwlock:
            if os.path.realpath(os.curdir) != self.gitdir:
                os.chdir(self.gitdir)
            if not os.path.exists('.git'):
                shell_do('git init')
            if not os.path.exists('.git-annex'):
                import socket
                shell_do('git annex init "%s"' % socket.gethostname())


    def __call__(self, op, path, *args):
        """
        redirects self.op('/foo', ...) to self.op('./foo', ...)
        """
        os.chdir(self.gitdir)   # when foreground is not set, the working
                                # directory changes unexplainably
        return super(ShareBox, self).__call__(op, "." + path, *args)

    getxattr = None
    listxattr = None
    link = None                 # No hardlinks
    mknod = None                # No devices
    mkdir = os.mkdir
    readlink = os.readlink
    rmdir = os.rmdir

    def statfs(self, path):
        stv = os.statvfs(path)
        return dict((key, getattr(stv, key)) for key in ('f_bavail',
            'f_bfree', 'f_blocks', 'f_bsize', 'f_favail', 'f_ffree',
            'f_files', 'f_flag', 'f_frsize', 'f_namemax'))

    def create(self, path, mode):
        return os.open(path, os.O_WRONLY | os.O_CREAT, mode)

    def utimens(self, path, times):
        if path == './.command':
            raise FuseOSError(EACCES)
        else:
            os.utime(path, times)

    def readdir(self, path, fh):
        """
        We have special files in the root to communicate with sharebox.
        """
        if path == './':
            return ['.', '..', '.command'] + os.listdir(path)
        else:
            return ['.', '..'] + os.listdir(path)

    def access(self, path, mode):
        """
        Annexed files can be accessed any mode as long are they are
        present.
        """
        if path == './.command':
            if mode & os.R_OK:
                raise FuseOSError(EACCES)
        else:
            if annexed(path):
                if not os.path.exists(path):
                    raise FuseOSError(EACCES)
            else:
                if not os.access(path, mode):
                    raise FuseOSError(EACCES)

    def open(self, path, flags):
        """
        When an annexed file is requested, if it is not present on the
        system we first try to get it. If it fails, we refuse the access.
        Since we do copy on write, we do not need to try to open in write
        mode annexed files.
        """
        if path == './.command':
            return os.open('/dev/null', flags)
        else:
            res = None
            if annexed(path):
                if not os.path.exists(path):
                    shell_do('git annex get "%s"' % path)
                if not os.path.exists(path):
                    raise FuseOSError(EACCES)
                res = os.open(path, os.R_OK) # magic to open read only
            else:
                res = os.open(path, flags)
            return res

    def getattr(self, path, fh=None):
        """
        When an annexed file is requested, we fake some of its attributes,
        making it look like a conventional file (of size 0 if if is not
        present on the system).

        FIXME: this method has too much black magic. We should find a way
        to show annexed files as regular and writable by altering the
        st_mode, not by replacing it.

        The file ./.command is a special file for communicating with the
        filesystem, we fake its attributes.
        """
        if path == './.command':
            # regular file, write-only, all the time attributes are 'now'
            return {'st_ctime': time.time(), 'st_mtime': time.time(),
                    'st_nlink': 1, 'st_mode': 32896, 'st_size': 0,
                    'st_gid': 1000, 'st_uid': 1000, 'st_atime':
                    time.time()}
        else:
            path_ = path
            faked_attr = {}
            if annexed(path):
                faked_attr ['st_mode'] = 33188 # we fake a 644 regular file
                if os.path.exists(path):
                    base = os.path.dirname(path_)
                    path_ = os.path.join(base, os.readlink(path))
                else:
                    faked_attr ['st_size'] = 0
            st = os.lstat(path_)
            res = dict((key, getattr(st, key)) for key in ('st_atime',
                'st_ctime', 'st_gid', 'st_mode', 'st_mtime',
                'st_nlink', 'st_size', 'st_uid'))
            for attr, value in faked_attr.items():
                res [attr] = value
            return res

    def chmod(self, path, mode):
        if path == './.command':
            raise FuseOSError(EACCES)
        else:
            with self.rwlock:
                with AnnexUnlock(path):
                    os.chmod(path, mode)

    def chown(self, path, user, group):
        if path == './.command':
            raise FuseOSError(EACCES)
        else:
            with self.rwlock:
                with AnnexUnlock(path):
                    os.chown(path, user, group)

    def truncate(self, path, length, fh=None):
        if path == './.command':
            return
        else:
            with self.rwlock:
                with AnnexUnlock(path):
                    with open(path, 'r+') as f:
                        f.truncate(length)

    def flush(self, path, fh):
        if path == './.command':
            return
        else:
            with self.rwlock:
                with CopyOnWrite(path, fh, self.opened_copies,
                        unlock=False, commit=False) as fh_:
                    os.fsync(fh_)

    def fsync(self, path, datasync, fh):
        if path == './.command':
            return
        else:
            with self.rwlock:
                with CopyOnWrite(path, fh, self.opened_copies,
                        unlock=False, commit=False) as fh_:
                    os.fsync(fh_)

    def read(self, path, size, offset, fh):
        if path == './.command':
            return
        else:
            with self.rwlock:
                with CopyOnWrite(path, fh, self.opened_copies,
                        unlock=False, commit=False) as fh_:
                    os.lseek(fh_, offset, 0)
                    return os.read(fh_, size)

    def write(self, path, data, offset, fh):
        if path == './.command':
            self.dotcommand(data)
            return len(data)
        else:
            with self.rwlock:
                with CopyOnWrite(path, fh, self.opened_copies,
                        unlock=True, commit=False) as fh_:
                    os.lseek(fh_, offset, 0)
                    return os.write(fh_, data)

    def release(self, path, fh):
        """
        Closed files are commited and removed from the open fd list
        """
        with self.rwlock:
            with CopyOnWrite(path, fh, self.opened_copies,
                    unlock=False, commit=True):
                os.close(fh)

    def rename(self, old, new):
        if old == './.command' or new == '/.command':
            raise FuseOSError(EACCES)
        else:
            with self.rwlock:
                # Make sure to lock the file (and to annex it if it was not)
                if not ignored(old):
                    shell_do('git annex add "%s"' % old)
                os.rename(old, '.' + new)
                if ignored(old) or ignored('.' + new):
                    if not ignored(old):
                        shell_do('git rm "%s"' % old)
                        shell_do('git commit -m "moved %s to ignored file"' % old)
                    if not ignored('.' + new):
                        shell_do('git annex add ".%s"' % new)
                        shell_do('git commit -m "moved an ignored file to .%s"' % new)
                else:
                    shell_do('git mv "%s" ".%s"' % (old, new))
                    shell_do('git commit -m "moved %s to .%s"' % (old, new))


    def symlink(self, target, source):
        if target == './.command':
            raise FuseOSError(EACCES)
        else:
            with self.rwlock:
                os.symlink(source, target)
                if not ignored(target):
                    shell_do('git annex add "%s"' % target)
                    shell_do('git commit -m "created symlink %s -> %s"' %(target,
                        source) )

    def unlink(self, path):
        if path == './.command':
            raise FuseOSError(EACCES)
        else:
            with self.rwlock:
                os.unlink(path)
                if not ignored(path):
                    shell_do('git rm "%s"' % path)
                    shell_do('git commit -m "removed %s"' % path)

    def dotcommand(self, text):
        for command in text.strip().split('\n'):
            if command == 'sync':
                self.sync()
            if command == 'merge':
                self.sync(True)
            if command.startswith('get '):
                shell_do('git annex ' + command)

    def sync(self, manual_merge=False):
        with sharebox.rwlock:
            shell_do('git fetch --all')
            repos = subprocess.Popen(
                    shlex.split('git remote show'),
                    stdout=subprocess.PIPE).communicate()[0].strip().split('\n')
            for remote in repos:
                if remote:
                    if not shell_do('git merge %s/master' % remote):
                        if manual_merge:
                            shell_do(notifycmd %
                                    "Manual merge invoked, but not implemented.")
                            shell_do('git reset --hard')
                            shell_do('git clean -f')
                        else:
                            shell_do('git reset --hard')
                            shell_do('git clean -f')
                            shell_do(notifycmd %
                                    "Manual merge is required. Run: \nsharebox --merge "+
                                    self.mountpoint)
                    else:
                        if self.getall:
                            shell_do('git annex get .')
                        shell_do('git commit -m "merged with %s"' % remote)

def send_sharebox_command(command, mountpoint):
    """
    send a command to the sharebox file system mounted on the mountpoint:
    write the command to the .command file on the root
    """
    if not shell_do('grep %s /etc/mtab' % mountpoint):
        print 'Mountpoint %s was not found in /etc/mtab' % mountpoint
        return 1
    else:
        valid_commands = ["merge", "get", "sync"]
        if not command.split()[0] in valid_commands:
            print '%s : unrecognized command' % command
            return 1
        else:
            with open(os.path.join(mountpoint, ".command"), 'w') as f:
                f.write(command)

if __name__ == "__main__":
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "ho:c:", ["help",
            "command="])
    except getopt.GetoptError, err:
        print str(err)
        print __doc__
        sys.exit(1)

    command = None
    gitdir = None
    getall = False
    numversions = 0
    notifycmd = 'notify-send "sharebox" "%s"'

    for opt, arg in opts:
        if opt in ("-h", "--help"):
            print __doc__
            sys.exit(0)
        if opt in ("-c", "--command"):
            command = arg
        if opt == "-o":
            if '=' in arg:
                option = arg.split('=')[0]
                value = arg.replace( option + '=', '', 1)
                if option == 'gitdir':
                    gitdir = value
                elif option == 'numversions':
                    numversions = int(value)
                elif option == 'notifycmd':
                    notifycmd = value
                else:
                    print("unrecognized option: %s" % option)
                    sys.exit(1)
            else:
                if arg == 'foreground':
                    foreground=True
                elif arg == 'getall':
                    getall=True
                else:
                    print("unrecognized option: %s" % arg)
                    sys.exit(1)

    mountpoint = "".join(args)
    if mountpoint == "":
        print 'invalid mountpoint'
        sys.exit(1)
    mountpoint = os.path.realpath(mountpoint)

    if command:
        retcode = send_sharebox_command(command, mountpoint)
        sys.exit(retcode)
    else:
        if not gitdir:
            print "Can't mount, missing the gitdir option."
            print __doc__
            sys.exit(1)
        gitdir = os.path.realpath(gitdir)

        sharebox = ShareBox(gitdir, mountpoint, numversions, getall)
        fuse = FUSE(sharebox, mountpoint, foreground=foreground)
