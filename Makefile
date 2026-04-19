.PHONY: install init step status clean test

install:
	uv sync

init:
	uv run novel-studio init "$(P)" --genre $(or $(G),科幻) --chapters $(or $(N),3)

step:
	uv run novel-studio step $(D)

status:
	uv run novel-studio status $(D)

test:
	uv run pytest -v

clean:
	rm -rf projects/*/queue projects/*/responses __pycache__ .pytest_cache
