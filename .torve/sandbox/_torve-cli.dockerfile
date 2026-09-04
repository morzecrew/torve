# The engine's own CLI, for the verbs an attempt calls from inside its
# sandbox: `torve log divergence` records a divergence through the run's
# channel, `torve log notes` reads what the engine had to say. Without it
# the prompt names commands the sandbox does not have.
#
# Its own interpreter and its own environment, under /opt/torve: the
# repository under work owns the workspace, and the intake must not depend
# on what that repository happens to install — or on it being a Python
# project at all. The interpreter is installed where every uid can read it,
# because a sandbox runs as the host's uid and uv's default install
# directory is root's home; the build proves it by running the verb as
# `nobody` rather than as the user that built it.
#
# Guarded like the battery's dependency layer, and for the same reason:
# `torve sandbox build` stages the project into the context, and a bare
# `docker build` of this directory still produces a working image without
# it.
#
# This block is byte-identical in every agent definition, copied from
# `.torve/sandbox/_torve-cli.dockerfile` and pinned against it by a test —
# edit that file, not this copy. A shared base image replaces the
# arrangement when the sandboxes are reworked.
COPY . /opt/torve/build-context/
ENV UV_PYTHON_INSTALL_DIR=/opt/torve/python
RUN set -eu; \
    if [ -f /opt/torve/build-context/pyproject.toml ] \
       && [ -f /opt/torve/build-context/uv.lock ]; then \
      uv python install 3.13; \
      uv venv --python 3.13 /opt/torve/cli; \
      UV_PROJECT_ENVIRONMENT=/opt/torve/cli \
        uv sync --locked --no-cache --no-dev --no-editable \
          --project /opt/torve/build-context; \
      ln -sf /opt/torve/cli/bin/torve /usr/local/bin/torve; \
      chmod -R a+rX /opt/torve/python /opt/torve/cli; \
      su -s /bin/sh -c "torve --version" nobody; \
    fi; \
    rm -rf /opt/torve/build-context
