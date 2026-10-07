import requests

# One pooled session per process: keep-alive connections cut latency on repeat calls.
session = requests.Session()
