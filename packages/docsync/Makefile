# docsync ships inside the maccluster wheel (repo root pyproject.toml) since maccluster 0.9.0.
# There is no separate docsync-lan wheel any more; build/release maccluster instead.
.PHONY: test
test:
	python3 -m unittest discover -s tests -v
