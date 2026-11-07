.PHONY: test install uninstall lint wheel release
PREFIX ?= $(HOME)/.local/bin
VERSION = $(shell python3 -c 'import docsync; print(docsync.__version__)')
WHEEL = dist/docsync_lan-$(VERSION)-py3-none-any.whl

# pip overwrites an existing wheel of the same version; older versions stay in dist/ (gitignored).
wheel:
	python3 -m pip wheel . --no-deps -w dist -q
	@ls $(WHEEL)

# Attach the wheel to the GitHub release of the current version (tag must exist).
release: wheel
	gh release upload v$(VERSION) $(WHEEL) --clobber

test:
	python3 -m unittest discover -s tests -v

install:
	mkdir -p $(PREFIX)
	ln -sf $(CURDIR)/bin/docsync $(PREFIX)/docsync
	@echo "installed $(PREFIX)/docsync"

uninstall:
	rm -f $(PREFIX)/docsync

lint:
	python3 -m pyflakes docsync tests 2>/dev/null || python3 -m compileall -q docsync tests
