debug-interactive: dirs
	python sharebox.py test/local/mnt -o gitdir=test/local/git -o foreground
	chmod -R +w test
	rm -rf test

test:
	@sh ./test.sh

test-interactive:
	@sh ./test.sh --interactive

unmount:
	@fusermount -u test/local/mnt

dirs:
	@mkdir -p test/local/mnt
	@mkdir -p test/local/git
	@cd test/local/git; git init

clean:
	rm -rf *.pyc
