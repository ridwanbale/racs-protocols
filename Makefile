.PHONY: install test simulate benchmark clean

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --cov=racs --cov-report=term-missing

simulate:
	python simulations/warehouse_sim.py

benchmark:
	python benchmarks/runner.py

quickstart:
	python examples/quickstart.py

demo:
	python examples/multi_site_coordination.py

safety-demo:
	python examples/safety_override_demo.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache dist build *.egg-info
