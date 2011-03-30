#!/usr/bin/env sh

init(){
    mkdir -p test/$1/mnt test/$1/git
    cd test/$1/git && git init && git annex init $1 && cd -
}

mount(){
    ./sharebox.py test/$1/mnt -o gitdir=test/$1/git -o sync=10
}

unmount(){
    fusermount -u test/$1/mnt
}

clean(){
    chmod -R +w test
    rm -rf test
}

mount_unmount(){
    init local
    mount local
    unmount local
    clean
}

mount_unmount
