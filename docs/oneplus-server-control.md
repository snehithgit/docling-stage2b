# OnePlus phone-script control

The web application no longer manages llama.cpp directly. It does not inspect
remote processes, discover GGUF files, read llama logs, capture command lines,
or manage Android wake locks.

It uses SSH only to invoke a phone-side script:

```text
$HOME/bin/oneplus-llama-control start
$HOME/bin/oneplus-llama-control restart
$HOME/bin/oneplus-llama-control stop
```

The bundled script is `mobile/oneplus-llama-control`. The OnePlus page includes
an **Install / update script** button that copies it to the Termux home over SSH.

The phone-side script owns:

- best-effort rooted launch of the Termux activity;
- `termux-wake-lock` / `termux-wake-unlock`;
- rooted Doze whitelist fallback for `com.termux`;
- PID and log files under `$HOME/.local/state/oneplus-llama/`;
- the proven Qwen3.5 2B + mmproj llama.cpp command;
- CPU affinity on cores `4,5,6,7` when `taskset` is available, leaving cores 0-3 for Android/sshd;
- `nice -n 10` when available;
- `-t 4 -tb 4 -c 4096 -np 1`, reasoning disabled, and `--image-max-tokens 1024`;
- start / restart / stop behavior.

The N150 keeps only the SSH connection settings and password environment name.
The SSH password is never sent to the browser.
