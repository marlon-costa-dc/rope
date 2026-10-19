Release Process
===============

Pre-Release
-----------

1. Update :ref:`gha-cache-key.txt <gha-cache-key>`:
   ``pip-compile --extra dev --generate-hashes -o gha-cache-key.txt --resolver=backtracking``

Release
-------

1. Ensure tickets assigned to Milestones are up to date
2. Update ``CHANGELOG.md``
3. Close milestone
4. Increment version number in ``pyproject.toml``
5. `git commit && git push`
6. Tag the release with the tag annotation containing the release information,
   ``python bin/tag-release.py``
7. Create Github Release
8. Publish release announcements to GitHub Discussions

Downstream Fork Releases
------------------------

Local-version releases (a ``+`` label) are distributed as GitHub Release wheel and
source-distribution assets, not uploaded to PyPI. The `version specification`_
prohibits local version identifiers on PyPI. Public upstream releases retain the
existing trusted-publisher workflow.

Run ``make setup fmt check test build`` on the release candidate. The native
``build`` target invokes ``artifact-check`` to verify typed markers and exercise
the installed wheel outside the checkout. After merging and creating the signed,
annotated version tag, create the GitHub Release with both validated artifacts.
The release workflow downloads those published assets and runs the same
``make artifact-check`` against them; a locally rebuilt wheel is not a substitute
for that published-artifact receipt.

.. _version specification: https://packaging.python.org/en/latest/specifications/version-specifiers/#local-version-identifiers


Release Schedule
================

Rope has a release schedule once a month, usually sometime close to the 15th of
each month. However, this schedule is not a guaranteed date, if there is a
particularly urgent change or if there's not enough pull requests for the
month, there may be additional releases or the release window may be skipped.
