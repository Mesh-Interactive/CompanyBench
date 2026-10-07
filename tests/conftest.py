"""The test suite must never call paid services or the public network."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    original_connect = socket.socket.connect

    def connect(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            pytest.fail(f"Offline test attempted a socket connection: {address}")
        return original_connect(sock, address)

    def lookup(*args, **kwargs):
        pytest.fail("Offline test attempted an unmocked DNS lookup")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
