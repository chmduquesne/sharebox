#!/usr/bin/env sh

# test suite for sharebox

if test $1; then
    interactive=1
fi

#-----------------------------------------------------------------------#
# base commands
#-----------------------------------------------------------------------#

init(){
    mkdir -p test/$1/mnt test/$1/git
    (cd test/$1/git && git init && git annex init $1 && cd -) >/dev/null
}

mount(){
    ./sharebox.py test/$1/mnt -o gitdir=test/$1/git
}

unmount(){
    fusermount -u -z test/$1/mnt >/dev/null
}

clean(){
    chmod -R +w test
    rm -rf test
}

remote_add(){
    (cd test/$1/git && git remote add $2 ../../$2/git && cd -) >/dev/null
}

make_peers(){
    remote_add $1 $2
    remote_add $2 $1
}

debug_interrupt(){
    if test $interactive; then
        echo "Press enter to continue."
        read anykey
    fi
}

test_must_success(){
    ($@) || echo "failure"
}

test_must_fail(){
    ($@) && echo "failure"
}

#-----------------------------------------------------------------------#
# tests
#-----------------------------------------------------------------------#

mount_unmount(){
    echo "mounting then demounting"
    init local
    mount local
    debug_interrupt
    unmount local
    clean
}

simple_sync(){
    echo "simple synchronization"
    init local
    init remote
    mount local
    mount remote
    make_peers local remote
    echo "test_line" >> test/local/mnt/test_file
    ./sharebox.py -c merge test/remote/mnt
    # after sync, the file must exist
    test_must_success test -e test/remote/mnt/test_file
    # but diffing should fail because it is recorded as size 0
    test_must_fail diff test/local/mnt/test_file test/remote/mnt/test_file > /dev/null
    # diffing should work the second time (opened by the first diff, so it
    # has been downloaded)
    test_must_success diff test/local/mnt/test_file test/remote/mnt/test_file
    debug_interrupt
    unmount local
    unmount remote
    clean
}

mount_unmount
simple_sync
