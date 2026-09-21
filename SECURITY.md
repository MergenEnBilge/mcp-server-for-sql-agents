# Security policy

## Reporting a problem

If you find a vulnerability, please don't open a public issue. Use GitHub's private
"Report a vulnerability" option on this repository's Security tab, and include what you found, how to
reproduce it, and which version or commit you tested. You'll get an answer as soon as I can give one.

## What is covered

This repository is a server that sits between AI agents and real databases, so the interesting
questions are: can an agent read something it wasn't granted, can it change data, can someone act
as somebody else, and can a stored credential leak.

[docs/security.md](docs/security.md) explains how each of those is prevented, which standards the
server is checked against, what the last review found, and what to do when running it for real.

## Supported versions

Only the latest commit on `main` gets fixes.
