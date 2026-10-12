"""Demo API - Identical code runs in the VM and in both containers"""
import hashlib
import os

from flask import Flask, jsonify, request

app = Flask(__name__)


"""Health check endpoint"""
@app.get("/health")
def health():
	# Used by the benchmark harness as the readiness probe.
	return jsonify(status="ok")


"""Returns the service name and hostname of the VM/Container"""
@app.get("/")
def index():
	return jsonify(service="cloud-project-demo", host=os.uname().nodename)


"""Calculates n chained SHA-256 hashes to benchmark CPU perforance"""
@app.get("/compute")
def compute():
	# Default to 5000 hashes (limited for safety)
	n = max(1, min(int(request.args.get("n", 5000)), 50000))

	# Initial input for the hash chain
	digest = b"cloud-project"

	# Each hash uses the previous hash as its input.
	for _ in range(n):
		digest = hashlib.sha256(digest).digest()

	# Return the hash count and part of the final digest.
	return jsonify(n=n, digest=digest.hex()[:16])
