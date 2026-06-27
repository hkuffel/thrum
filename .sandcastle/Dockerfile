FROM node:22-bookworm

# Install system dependencies. thrum is a pure-Python uv project; make/
# build-essential cover any wheels uv builds from source, and curl + jq are
# generally useful inside agent runs.
RUN apt-get update && apt-get install -y \
  git \
  curl \
  jq \
  make \
  python3 \
  python3-venv \
  build-essential \
  && rm -rf /var/lib/apt/lists/*

# Install uv (Python package manager — drives `uv sync` / `uv run` at the repo root).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install GitHub CLI — the issue tracker is GitHub and the sandcastle prompts
# use `gh issue ...`.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && apt-get update && apt-get install -y gh \
  && rm -rf /var/lib/apt/lists/*

# Build-args for UID/GID alignment: sandcastle docker build-image
# defaults these to the host user's UID/GID so image-built files
# and bind-mounted files share an owner without runtime chown.
ARG AGENT_UID=1000
ARG AGENT_GID=1000

# Install pi coding agent CLI.
RUN npm install -g @earendil-works/pi-coding-agent

# Rename the base image's "node" user to "agent" and align UID/GID.
RUN groupmod -o -g $AGENT_GID node && usermod -o -u $AGENT_UID -g $AGENT_GID -d /home/agent -m -l agent node
USER ${AGENT_UID}:${AGENT_GID}

# Let pi install configured packages/themes as the unprivileged agent user.
# Host settings may include global npm packages; npm's default global prefix
# (/usr/local) is root-owned in the image.
ENV NPM_CONFIG_PREFIX="/home/agent/.local"
ENV PATH="/home/agent/.local/bin:$PATH"
RUN mkdir -p /home/agent/.local /home/agent/.npm

WORKDIR /home/agent

# In worktree sandbox mode, Sandcastle bind-mounts the git worktree at /home/agent/workspace
# and overrides the working directory to /home/agent/workspace at container start.
# Structure your Dockerfile so that /home/agent/workspace can serve as the project root.
ENTRYPOINT ["sleep", "infinity"]
