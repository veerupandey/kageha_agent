"""Spec-driven development pipeline.

Flow: prompt → requirements → design → tasks → parallel build.
Each stage produces a persistent artifact under .kageha/specs/<feature>/
with validation gates between stages.
"""
