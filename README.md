# pod-pi

Named, persistent [Pi](https://pi.dev/) coding workspaces in rootless Podman containers.

```bash
pod-pi create "research"
research-pi
```

The first launch builds the image, starts the container, and attaches to Pi in
tmux. Later launches reconnect to the same session. Detach with **Ctrl-b, then d**;
Pi and any running work continue after the terminal or SSH connection closes.

Built from the persistent workspace approach used by `spark-pi` and `marsay-pi`.
The default runs without a GPU, a particular model server, or systemd. An optional
NVIDIA profile carries the original PyTorch development environment and extension
set. A model runs through your configured provider; pod-pi does not provision a
model server.

## Install

Requires **Linux**, **Python 3.10+**, and working **rootless Podman** on the host.
The default Debian-based image supports amd64 and arm64. Other host operating
systems, remote Podman machines, and Docker are not currently supported.

```bash
git clone https://github.com/Hackers-in-the-Loop/pod-pi.git
cd pod-pi
./bin/pod-pi install
export PATH="$HOME/.local/bin:$PATH"  # also add to your shell startup file
pod-pi create "research"
research-pi
```

Keep the checkout in place: installed commands refer to it. Updating the checkout
updates the host launcher, while existing instances keep their own image recipes
and settings. Installation does not change host packages, drivers or services.
Check `podman info` if the container engine is not yet configured for your user.

`create` writes files and the launcher. It defers downloads and container startup
until the first launch, giving you time to configure the instance. Use
`pod-pi create research --start` to build and start immediately without attaching.
Names start with a lowercase letter and contain lowercase letters, digits and
hyphens. The command appends `-pi`: use `research`, not `research-pi`, as the name.
Existing commands and instance directories are never overwritten.

## Choose a model

On first attach, use Pi's `/login` and `/model`, or configure the instance before
starting it:

```bash
research-pi path
# Edit ~/.local/share/pod-pi/research/home/.pi/agent/settings.json
# Put custom providers in that same directory's models.json
```

[examples/models.json](examples/models.json) shows a generic OpenAI-compatible
endpoint. Replace its endpoint and model ID, then copy it into the instance's
`home/.pi/agent/models.json`. Set `defaultProvider` and `defaultModel` in that
instance's `settings.json` if you want to select it by default. See Pi's
[model configuration](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md)
and [provider documentation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/providers.md).

Put API keys and other environment variables in the instance's private `env`
file, one `KEY=value` per line (without shell `export` or quotes). The host's
environment and credentials are not automatically forwarded. Changes to `env`
or container runtime options require `stop` followed by `recreate`. In a
container, `localhost` refers to that container; a service on the host can often
be reached using `host.containers.internal`. Set explicit `--add-host` arguments
in `instance.json` if your network requires them.

## Configure extensions

Pi packages are the extension list in the instance's normal
`home/.pi/agent/settings.json`:

```json
{
  "theme": "dark",
  "packages": [
    "npm:pi-web-access@0.29.0",
    "npm:pi-subagents@0.68.0",
    "npm:pi-goal-x@0.31.4",
    "npm:@dietrichgebert/ponytail@4.10.0"
  ]
}
```

The default profile has an empty list. Choose packages at creation with
`pod-pi create research --extensions ./examples/extensions.json`. Use a JSON
file containing `[]` for no packages, including when using the NVIDIA profile.

Before Pi starts, the container installs packages when the configured list has
changed. Subsequent starts reuse the installed packages. After editing the list
on a running instance, run `research-pi stop` and `research-pi start`, then attach.
Removing a package from the list disables its resources; cached files remain.
Pin versions for repeatable installs. Package objects with `source` and resource
filters are also supported. Local package paths refer to paths **inside** the
container. Pi's `extensions` setting also supports local extension files.

Other settings, provider credentials and extension-specific configuration live
beside `settings.json`; pod-pi does not impose model defaults on extensions.
See Pi's [package documentation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md).

For defaults applied to **future** instances, create
`~/.config/pod-pi/defaults.json`. For example:

```json
{
  "settings": {
    "defaultThinkingLevel": "medium",
    "packages": ["npm:pi-goal-x@0.31.4"]
  },
  "create_args": ["--shm-size", "2g", "--memory", "8g"]
}
```

Precedence is profile → user defaults → `--extensions`. Dictionary fields merge
one level deep; arrays replace the previous array. Existing instances are not
changed. `XDG_CONFIG_HOME` changes the configuration base directory.

## Daily commands

| Command | Action |
| --- | --- |
| `research-pi` | Start if needed and attach to Pi |
| `research-pi shell` | Open another shell in `/workspace` |
| `research-pi start` | Build/create if needed, start, wait for the terminal |
| `research-pi stop` | Stop this instance and its running jobs |
| `research-pi status` | Show container state and instance path |
| `research-pi logs` | Show recent startup logs |
| `research-pi check` | Check Pi, package registration, writable mounts and tmux |
| `research-pi path` | Print the instance directory |
| `research-pi build` | Build the current image recipe |
| `research-pi recreate` | Replace a stopped container, preserve home/workspace, start |
| `research-pi enable` | Enable and start an optional systemd user service |
| `research-pi disable` | Disable service autostart; leave current work running |
| `pod-pi list` | List managed instances |
| `pod-pi profiles` | List built-in profiles |

`pod-pi run research <action>` works without the generated launcher on PATH.
Use an interactive terminal or `ssh -t` to attach. Tmux window 0 runs Pi and
window 1 is a shell; **Ctrl-b 0/1** switches windows. If Pi exits, run `pi-session`
in its window to reopen the last saved conversation.

Stopping or rebooting ends running jobs. Starting reopens the latest saved Pi
conversation; it does not automatically resume an interrupted turn or goal.
For boot startup without logging in, enable the instance service and enable
lingering for your account with `loginctl enable-linger "$USER"` (host policy may
require administrator assistance). Systemd is optional for ordinary launch and
reconnect. An enabled service supervises container failures; without a service,
run the launcher again to restart a failed container.

## Customize a container

Instances live outside the repository:

```text
~/.local/share/pod-pi/research/
  instance.json       # build arguments, Podman create arguments, optional image
  container/          # editable Containerfile, entrypoint and tmux setup
  env                 # private container environment
  workspace/          # mounted at /workspace
  home/               # mounted at /root, including .pi/agent
```

Set `POD_PI_HOME` and `POD_PI_BIN_DIR`, or pass `--data-dir` and `--bin-dir`
before the subcommand, to choose other locations. Generated launchers remember
the data location. Instance paths may contain spaces, but not colons or newlines.

Ask an agent, for example:

> Customize the research pod-pi instance for a Rust project. Inspect its path,
> add the compiler to its Containerfile, and build the image. Preserve its home
> and workspace. Leave any active workload running.

Add OS dependencies to `container/Containerfile`; tune devices, networking,
resource limits and extra mounts using the `create_args` array in `instance.json`.
Each array element is one Podman argument: no shell parsing or command substitution
occurs. These are trusted runtime options; adding privileged access or host mounts
changes what the container can access. The built-in image recipe requires an
apt-based base image with glibc compatible with Node 22; edit the Containerfile for
other distributions. Pi itself is pinned to 0.84.4 from the working foundation;
the Node/Debian tags can move. Pin image digests in a custom recipe when full
base-image reproducibility is required.

An `image` field uses a prebuilt image instead of the local recipe. It must supply
this project's entrypoint contract: persistent `/root`, writable `/workspace`,
Pi and a tmux session named `pi`. `build` pulls that image. Otherwise, image tags
are derived from the build arguments and recipe contents so identical instances
reuse layers and customized recipes get different tags.

Building does not replace an existing container. Once its jobs can stop:

```bash
research-pi stop
research-pi recreate
```

`recreate` discards the container's writable layer, including manually installed
system packages; home and workspace survive. It refuses to replace a running
container and builds before removing the old one. Back up home and workspace
separately. Stuck or foreign containers are left intact for inspection rather than
automatically recovered or removed.

## NVIDIA and other hardware

```bash
pod-pi create gpu-work --profile nvidia
gpu-work-pi
```

The NVIDIA profile uses the original pinned NVIDIA PyTorch 25.11 image and four
Pi packages. It requires a compatible NVIDIA driver, NVIDIA Container Toolkit,
and a generated NVIDIA CDI device specification on the host. It requests all
GPUs, 16 GiB shared memory, unlimited memlock and SYS_PTRACE. It does not set a
GB10-only compute architecture, private hostname or model. Set workload-specific
CUDA architecture variables in the instance's `env` if needed. Compatibility
with a particular GPU still depends on the selected NVIDIA image and driver.

For AMD, other accelerators, or a different base image, copy a profile and edit
its build/runtime options, then use `--profile /path/to/profile.json`. The
built-in NVIDIA recipe is a starting point, not an automatic driver installer.
Host setup and hardware-specific CUDA/Triton checks from the original deployment
are intentionally left to the target environment.

Containers run as root **inside rootless Podman**, mapped to your ordinary host
user. Their own home/workspace and any extra configured mounts are accessible to
Pi; the host home and container-engine socket are not mounted by default. The
containers have network access and no default host-memory quota.

## Development

```bash
python3 -m unittest discover -s tests -v
bash -n container/entrypoint container/pi-session
```

The tests cover isolated creation, configuration precedence, collision refusal,
launcher quoting, ownership checks, lifecycle safeguards and extension syncing.
[AGENTS.md](AGENTS.md) explains where to make launcher and profile changes.
Runtime checks do not verify model inference or GPU correctness. Test those with
your chosen endpoint and hardware. Do not commit instance homes, workspaces,
credentials or local server configuration.

MIT licensed. See [LICENSE](LICENSE).
