#!/usr/bin/env python
"""
Filesystem based on git-annex.

Usage

About git-annex: git-annex allows to keep only links to files and to keep
their content out of git. Once a file is added to git annex, it is
replaced by a symbolic link to the content of the file. The content of the
file is made read-only so that you don't modify it by mistake.

What does this file system:
- It automatically adds new files to git-annex.
- It resolves git-annex symlinks so that you see them as regular writable
  files.
- If the content of a file is not present on the file system, it is
  requested on the fly from one of the replicated copies.
- When you access a file, it does copy on write: if you don't modify it,
  you read the git-annex copy. However, if you change it, the copy is
  unlocked on the fly and commited to git-annex when closed. Depending on
  the mount option, the previous copy can be kept in git-annex.
- It pulls at regular intervals the other replicated copies and launches a
  merge program if there are conflicts.
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

def ignored(path):
    """
    Returns true if we should ignore this file, false otherwise. This
    should respect the different ways for git to ignore a file.
    """
    path_ = path[2:]
    # Exception: files that are versionned by git but that we want to ignore
    if (os.path.commonprefix([path_, '.git-annex/']) == '.git-annex/' or
        os.path.commonprefix([path_, '.git/']) == '.git/'):
        return True
    else:
        ls_options = "-c -o -d -m --full-name --exclude-standard"
        considered = subprocess.Popen(
                shlex.split('git ls-files %s -- "%s"' % (ls_options, path_)),
                stdout=subprocess.PIPE).communicate()[0].split('\n')
        if path_ not in considered:
            print '%s is ignored' % path_
            print considered
        else:
            print '%s is considered' % path_
            print considered
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
    print cmd
    p = subprocess.Popen(shlex.split(cmd))
    p.wait()

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
        self.ignored = ignored(path)

    def __enter__(self):
        if not self.ignored:
            shell_do('git annex unlock "%s"' % self.path)

    def __exit__(self, type, value, traceback):
        if not self.ignored:
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
                if not ignored(self.path):
                    shell_do('git annex unlock "%s"' % self.path)
                self.opened_copies[self.fh] = os.open(self.path,
                        os.O_WRONLY | os.O_CREAT)
        return self.opened_copies.get(self.fh, self.fh)

    def __exit__(self, type, value, traceback):
        if self.commit:
            if self.opened_copies.get(self.fh, None) != None:
                if not ignored(self.path):
                    try:
                        os.close(self.opened_copies[self.fh])
                        del self.opened_copies[self.fh]
                    except KeyError:
                        pass
                    shell_do('git annex add "%s"' % self.path)
                    shell_do('git commit -m "changed %s"' % self.path)

class ShareBox(LoggingMixIn, Operations):
    """
    Assumes operating from the root of the managed git directory
    """
    def __init__(self, gitdir):
        self.gitdir = gitdir
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
        if os.path.realpath(os.curdir) != self.gitdir:
            os.chdir(self.gitdir)
        return super(ShareBox, self).__call__(op, "." + path, *args)

    getxattr = None
    listxattr = None
    link = os.link
    mknod = os.mknod
    mkdir = os.mkdir
    readlink = os.readlink
    rmdir = os.rmdir

    def statfs(self, path):
        stv = os.statvfs(path)
        return dict((key, getattr(stv, key)) for key in ('f_bavail',
            'f_bfree', 'f_blocks', 'f_bsize', 'f_favail', 'f_ffree',
            'f_files', 'f_flag', 'f_frsize', 'f_namemax'))

    def create(self, path, mode):
        with self.rwlock:
            fh = os.open(path, os.O_WRONLY | os.O_CREAT, mode)
            with CopyOnWrite(path, fh, self.opened_copies, unlock=True,
                    commit=False):
                return fh

    def utimens(self, path, times):
        os.utime(path, times)

    def readdir(self, path, fh):
        return ['.', '..'] + os.listdir(path)

    def access(self, path, mode):
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

        FIXME: this method has too much black magic. We should just alter
        the write permission bit when opening. I don't think it is going
        to work with executables.
        """
        res = None
        if annexed(path):
            if not os.path.exists(path):
                shell_do('git annex get "%s"' % path)
            if not os.path.exists(path):
                raise FuseOSError(EACCES)
            res = os.open(path, 32768) # magic to open read only
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
        """
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
        with self.rwlock:
            with AnnexUnlock(path):
                os.chmod(path, mode)

    def chown(self, path, user, group):
        with self.rwlock:
            with AnnexUnlock(path):
                os.chown(path, user, group)

    def truncate(self, path, length, fh=None):
        with self.rwlock:
            with AnnexUnlock(path):
                with open(path, 'r+') as f:
                    f.truncate(length)

    def flush(self, path, fh):
        with self.rwlock:
            with CopyOnWrite(path, fh, self.opened_copies, unlock=False,
                    commit=False) as fh_:
                os.fsync(fh_)

    def fsync(self, path, datasync, fh):
        with self.rwlock:
            with CopyOnWrite(path, fh, self.opened_copies, unlock=False,
                    commit=False) as fh_:
                os.fsync(fh_)

    def read(self, path, size, offset, fh):
        with self.rwlock:
            with CopyOnWrite(path, fh, self.opened_copies, unlock=False,
                    commit=False) as fh_:
                os.lseek(fh_, offset, 0)
                return os.read(fh_, size)

    def write(self, path, data, offset, fh):
        with self.rwlock:
            with CopyOnWrite(path, fh, self.opened_copies, unlock=True,
                    commit=False) as fh_:
                os.lseek(fh_, offset, 0)
                return os.write(fh_, data)

    def release(self, path, fh):
        """
        Closed files are commited and removed from the open fd list
        """
        with self.rwlock:
            with CopyOnWrite(path, fh, self.opened_copies, unlock=False,
                    commit=True) as fh_:
                os.close(fh)

    def rename(self, old, new):
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
                    shell_do('git annex add "%s"' % new)
                    shell_do('git commit -m "moved an ignored file to %s"' % new)
            else:
                shell_do('git mv "%s" "%s"' % (old, '.' + new))
                shell_do('git commit -m "moved %s to .%s"' % (old, new))


    def symlink(self, target, source):
        with self.rwlock:
            os.symlink(source, target)
            if not ignored(target):
                shell_do('git annex add "%s"' % target)
                shell_do('git commit -m "created symlink %s -> %s"' %(target,
                    source) )

    def unlink(self, path):
        with self.rwlock:
            os.unlink(path)
            if not ignored(path):
                shell_do('git rm "%s"' % path)
                shell_do('git commit -m "removed %s"' % path)

def synchronize():
    """
    Place to introduce the synchonization daemon
    """
    i = 0
    while 1:
        time.sleep(1)
        i += 1

if __name__ == "__main__":
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "ho:", ["help"])
    except getopt.GetoptError, err:
        print str(err)
        print 'usage: %s <mountpoint> [-o <option>]' % sys.argv[0]
        sys.exit(1)

    gitdir = None
    logfile = 'sharebox.log'
    foreground = False
    sync = 0

    for opt, arg in opts:
        if opt in ("-h", "--help"):
            print 'usage: %s <mountpoint> [-o <option>]' % sys.argv[0]
            sys.exit(0)
        if opt == "-o":
            if '=' in arg:
                option = arg.split('=')[0]
                value = arg.replace( option + '=', '', 1)
                if option == 'gitdir':
                    gitdir = value
                if option == 'logfile':
                    logfile = value
                if option == 'sync':
                    sync = value
            else:
                if arg == 'foreground':
                    foreground=True

    if not gitdir:
        print 'missing the gitdir option'
        sys.exit(1)

    mountpoint = "".join(args)
    if mountpoint == "":
        print 'invalid mountpoint'
        sys.exit(1)

    if sync:
        t = threading.Thread(target=synchronize)
        t.daemon = True
        t.start()

    mountpoint = os.path.realpath(mountpoint)
    gitdir = os.path.realpath(gitdir)
    fuse = FUSE(ShareBox(gitdir), mountpoint, foreground=foreground)
