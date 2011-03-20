test: dirs
	python sharebox.py test/local/mnt -o gitdir=test/local/git

debug-interactive: dirs
	python sharebox.py test/local/mnt -o gitdir=test/local/git -o foreground

unmount:
	fusermount -u test/local/mnt
	#fusermount -u test/remote/mnt

dirs:
	@mkdir -p test/local/mnt
	@mkdir -p test/local/git
	@mkdir -p test/remote/mnt
	@mkdir -p test/remote/git

clean:
	rm -rf *.pyc
