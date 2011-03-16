#!/usr/bin/env python

from __future__ import with_statement

from errno import EACCES
from os.path import realpath
from sys import argv, exit
import threading

import os

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn

import shlex
import subprocess
import logging
import time

def ignored(path):
    """
    Returns true if we should ignore this file, false otherwise. This
    should respect the different ways for git to ignore a file.
    """
    path_ = path[:2]
    # Exception: files that are versionned by git but that we want to ignore
    if (os.path.commonprefix([path_, '.git-annex/']) == '.git-annex/' or
        os.path.commonprefix([path_, '.git/']) == '.git/'):
        return True
    else:
        considered = subprocess.Popen(
                shlex.split("git ls-files -c -o -d -m --exclude-standard"),
                stdout=subprocess.PIPE).communicate()[0].split()
        return path[2:] not in considered

def annexed(path):
    """
    returns True if the file is annexed, false otherwise
    """
    return (os.path.islink(path) and
            os.path.commonprefix([
                os.readlink(path),
                '.git/annex/objects']) == '.git/annex/objects')

def shell_do(cmd):
    """
    calls the given shell command
    """
    logging.debug(cmd)
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
            shell_do('git annex unlock %s' % self.path)

    def __exit__(self, type, value, traceback):
        if not self.ignored:
            shell_do('git annex add %s' % self.path)
            shell_do('chmod +w %s' % os.readlink(self.path))
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
        self.ignored = ignored(path)

    def __enter__(self):
        if self.unlock:
            if self.opened_copies.get(self.fh, None) == None:
                if not self.ignored:
                    shell_do('git annex unlock %s' % self.path)
                    self.opened_copies[self.fh] = os.open(self.path,
                            os.O_WRONLY | os.O_CREAT)
        return self.opened_copies.get(self.fh, self.fh)

    def __exit__(self, type, value, traceback):
        if self.commit:
            if not self.ignored:
                try:
                    os.close(self.opened_copies[self.fh])
                    del self.opened_copies[self.fh]
                except KeyError:
                    pass
                shell_do('git annex add %s' % self.path)
                shell_do('chmod +w %s' % os.readlink(self.path))
                shell_do('git commit -m "changed %s"' % self.path)

class ShareBox(LoggingMixIn, Operations):
    """
    Assumes operating from the root of the managed git directory
    """
    def __init__(self):
        self.rwlock = threading.Lock()
        self.opened_copies = {}
        with self.rwlock:
            if not os.path.exists('.git'):
                shell_do('git init')
            if not os.path.exists('.git-annex'):
                import socket
                shell_do('git annex init "%s"' % socket.gethostname())


    def __call__(self, op, path, *args):
        """
        redirects self.op('/foo', ...) to self.op('./foo', ...)
        """
        return super(ShareBox, self).__call__(op, "." + path, *args)

    getxattr = None
    listxattr = None
    link = os.link
    mknod = os.mknod
    mkdir = os.mkdir
    open = os.open
    readlink = os.readlink
    rmdir = os.rmdir

    def create(self, path, mode):
        return os.open(path, os.O_WRONLY | os.O_CREAT, mode)

    def utimens(self, path, times):
        os.utime(path, times)

    def readdir(self, path, fh):
        return ['.', '..'] + os.listdir(path)

    def access(self, path, mode):
        if not os.access(path, mode):
            raise FuseOSError(EACCES)

    def getattr(self, path, fh=None):
        """
        symlinks to annexed files are dereferenced
        """
        path_ = path
        if annexed(path) and os.path.exists(path):
            path_ = os.readlink(path)
        st = os.lstat(path_)
        return dict((key, getattr(st, key)) for key in ('st_atime',
            'st_ctime', 'st_gid', 'st_mode', 'st_mtime', 'st_nlink',
            'st_size', 'st_uid'))

    def statfs(self, path):
        """
        symlinks to annexed files are dereferenced
        """
        path_ = path
        if annexed(path) and os.path.exists(path):
            path_ = os.readlink(path)
        stv = os.statvfs(path_)
        return dict((key, getattr(stv, key)) for key in ('f_bavail',
            'f_bfree', 'f_blocks', 'f_bsize', 'f_favail', 'f_ffree',
            'f_files', 'f_flag', 'f_frsize', 'f_namemax'))

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
        # Make sure to lock the file (and to annex it if it was not)
        with self.rwlock:
            if not ignored(old):
                shell_do('git annex add %s' % old)
            os.rename(old, '.' + new)
            if ignored(old) or ignored('.' + new):
                if not ignored(old):
                    shell_do('git rm %s' % old)
                    shell_do('git commit -m "moved %s to ignored file"' % old)
                if not ignored('.' + new):
                    shell_do('git annex add %s' % new)
                    shell_do('git commit -m "moved an ignored file to %s"' % new)
            else:
                shell_do('git mv %s %s' % (old, '.' + new))
                shell_do('git commit -m "moved %s to .%s"' % (old, new))


    def symlink(self, target, source):
        with self.rwlock:
            os.symlink(source, target)
            if not ignored(target):
                shell_do('git annex add %s' % target)
                shell_do('git commit -m "created symlink %s -> %s"' %(target,
                    source) )

    def unlink(self, path):
        with self.rwlock:
            os.unlink(path)
            if not ignored(path):
                shell_do('git rm %s' % path)
                shell_do('git commit -m "removed %s"' % path)


def synchronize():
    """
    Place to introduce the synchonization daemon
    """
    i = 0
    while 1:
        logging.debug("synchronizing %s" % i)
        time.sleep(1)
        i += 1

if __name__ == "__main__":
    if len(argv) != 3:
        print 'usage: %s <gitdir> <mountpoint>' % argv[0]
        exit(1)
    logging.basicConfig(filename="sharebox.log", level=logging.DEBUG)
    t = threading.Thread(target=synchronize)
    t.daemon = True
    #t.start()
    mountpoint = realpath(argv[2])
    gitdir = realpath(argv[1])
    os.chdir(gitdir)
    fuse = FUSE(ShareBox(), mountpoint, foreground=True)
