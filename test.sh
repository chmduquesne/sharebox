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

mount_autosync(){
    ./sharebox.py test/$1/mnt -o gitdir=test/$1/git -o sync=1
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
    ($@ 2>&1) >/dev/null || echo "Error: This failed: $@"
}

test_must_fail(){
    ($@ 2>&1) >/dev/null && echo "Error: This succeeded: $@"
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

sync_simple(){
    echo "simple synchronization"
    init local
    init remote
    mount local
    mount remote
    make_peers local remote
    echo "test_line" >> test/local/mnt/test_file
    ./sharebox.py -c sync test/remote/mnt
    # after sync, the file must exist
    test_must_success test -e test/remote/mnt/test_file
    # but diffing should fail because it is recorded as size 0
    test_must_fail diff test/local/mnt/test_file test/remote/mnt/test_file
    # diffing should work the second time (opened by the first diff, so it
    # has been downloaded)
    test_must_success diff test/local/mnt/test_file test/remote/mnt/test_file
    debug_interrupt
    unmount local
    unmount remote
    clean
}

sync_normal_conflict(){
    echo "synchronization with normal conflict"
    init local
    init remote
    mount local
    mount remote
    make_peers local remote
    echo "test_line" >> test/local/mnt/test_file
    ./sharebox.py -c sync test/remote/mnt
    test_must_success test -e test/remote/mnt/test_file
    touch test/remote/mnt/test_file
    echo "test_line_local" >> test/local/mnt/test_file
    echo "test_line_remote" >> test/remote/mnt/test_file
    ./sharebox.py -c sync test/remote/mnt
    # but diffing should fail
    test_must_fail diff test/local/mnt/test_file test/remote/mnt/test_file
    debug_interrupt
    unmount local
    unmount remote
    clean
}

sync_delete_conflict(){
    echo "synchronization with delete conflict"
    init local
    init remote
    mount local
    mount remote
    make_peers local remote
    echo "test_line" >> test/local/mnt/test_file
    ./sharebox.py -c sync test/remote/mnt
    test_must_success test -e test/remote/mnt/test_file
    touch test/remote/mnt/test_file
    echo "test_line_remote" >> test/remote/mnt/test_file
    rm test/local/mnt/test_file
    ./sharebox.py -c sync test/remote/mnt
    # diffing should fail
    test_must_fail diff test/local/mnt/test_file test/remote/mnt/test_file
    debug_interrupt
    unmount local
    unmount remote
    clean
}

mount_unmount
sync_simple
sync_normal_conflict
sync_delete_conflict
