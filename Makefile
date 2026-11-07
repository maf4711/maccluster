.PHONY: test install uninstall lint
PREFIX ?= $(HOME)/.local/bin

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
