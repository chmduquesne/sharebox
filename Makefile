test: dirs
	python sharebox.py test/remote/mnt -o gitdir=test/remote/git -o sync=10
	python sharebox.py test/local/mnt -o gitdir=test/local/git -o sync=10 -o foreground

dirs:
	@mkdir -p test/local/mnt
	@mkdir -p test/local/git
	@cd test/local/git; git init
	@mkdir -p test/remote/mnt
	@mkdir -p test/remote/git
	@cd test/remote/git; git init
	@cd test/local/git; git remote add remote ../../remote/git
	@cd test/remote/git; git remote add local ../../local/git

debug-interactive: dirs
	python sharebox.py test/local/mnt -o gitdir=test/local/git -o foreground -o sync=10

unmount:
	fusermount -u test/local/mnt
	fusermount -u test/remote/mnt

touch: dirs
	@echo "testing file creation with touch"
	@python sharebox.py test/local/mnt -o gitdir=test/local/git
	@touch test/local/mnt/foo
	@rm -f test/local/mnt/foo
	@(readlink test/local/git/foo && echo success) || echo failed
	@fusermount -u test/local/mnt

dd: dirs
	@echo "testing file creation with dd"
	@python sharebox.py test/local/mnt -o gitdir=test/local/git
	@dd if=/dev/urandom of=test/local/mnt/foo bs=1M count=10
	@rm -f test/local/mnt/foo
	@(readlink test/local/git/foo && echo success) || echo failed
	@fusermount -u test/local/mnt

readlink: dirs
	@echo "testing annexed files appear as regular files"
	@python sharebox.py test/local/mnt -o gitdir=test/local/git
	@dd if=/dev/urandom of=test/local/git/foo bs=1M count=10
	@cd test/local/git; git annex add foo; git commit -m "test"
	@(readlink test/local/mnt/foo && echo failed) || echo success
	@rm -f test/local/mnt/foo
	@fusermount -u test/local/mnt


clean:
	rm -rf *.pyc
