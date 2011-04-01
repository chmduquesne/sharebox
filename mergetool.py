#!/usr/bin/env python
"""
Inspired from git-mergetool
"""

import os
import sys
import shlex
import subprocess
import os.path

def is_link(mode):
    return mode == "120000"

def files_to_merge():
    # To avoid duplication, we insert file paths as keys of a map
    unique_files = {}
    git_ls_files = subprocess.Popen(shlex.split('git ls-files -u'),
            stdout=subprocess.PIPE).communicate()[0].split("\n")
    for i in git_ls_files:
        if i:
            path = i.split("\t")[1]
            unique_files[path] = None
    return unique_files.keys()

def resolve_deleted_conflict(path):
    return True

def resolve_symlink_conflict(path):
    return True

def resolve_conflict(path):
    f = subprocess.Popen(shlex.split('git ls-files -u -- %s' % path),
            stdout=subprocess.PIPE).communicate()[0]
    if not f:
        return False

    local_mode = None
    remote_mode = None

    for i in f.split("\n"):
        if i:
            fields = i.split()
            mode = fields[0]
            comes_from = fields[2]
            if comes_from == "2":
                local_mode = mode
            if comes_from == "3":
                remote_mode = mode

    if not local_mode:
        print "%s was removed locally and modified remotely" % path
        return resolve_deleted_conflict(path)
    elif not remote_mode:
        print "%s was modified locally and removed remotely" % path
        return resolve_deleted_conflict(path)
    elif is_link(local_mode) and is_link(remote_mode):
        print "%s was modified locally and remotely" % path
        return resolve_symlink_conflict(path)
    else:
        if not is_link(local_mode):
            print "Anormal conflict. %s is not a link locally" % path
        else:
            print "Anormal conflict. %s is not a link remotely" % path
    return False


def continue_after_failed_merge():
    while True:
        answer = raw_input("Do you want to continue merging? (y/n) ")
        if answer.startswith("y") or answer.startswith("Y"):
            return True
        elif answer.startswith("n") or answer.startswith("N"):
            return False

def merge():
    merge_successful = True
    for i in files_to_merge():
        resolve_successful = resolve_conflict(i)
        merge_successful = merge_successful and resolve_successful
        if not resolve_successful:
            if not continue_after_failed_merge():
                break
    return merge_successful

if __name__ == "__main__":
    if merge():
        print "Merged sucessfully."
    else:
        print "Merged failed."
