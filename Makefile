debug-interactive: dirs
	python sharebox.py test/local/mnt -o gitdir=test/local/git -o foreground -o notifycmd='foo "%s"'
	chmod -R +w test
	rm -rf test

unmount:
	fusermount -u test/local/mnt

dirs:
	@mkdir -p test/local/mnt
	@mkdir -p test/local/git
	@cd test/local/git; git init

clean:
	rm -rf *.pyc
