from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected one anchor in {path}, found {count}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


p = Path("app/stage2b.py")

replace_once(
    p,
    '        self._colab_artifact_tasks: dict[str, asyncio.Task[Any]] = {}\n',
    '        self._colab_normal_tasks: dict[str, asyncio.Task[Any]] = {}\n'
    '        self._colab_artifact_tasks: dict[str, asyncio.Task[Any]] = {}\n',
)

replace_once(
    p,
    '            asyncio.create_task(self._device_loop("oneplus"), name="stage2b-oneplus-worker"),\n'
    '            asyncio.create_task(self._colab_artifact_dispatch_loop(), name="stage2b-colab-artifact-dispatcher"),\n',
    '            asyncio.create_task(self._device_loop("oneplus"), name="stage2b-oneplus-worker"),\n'
    '            asyncio.create_task(self._colab_normal_dispatch_loop(), name="stage2b-colab-normal-dispatcher"),\n'
    '            asyncio.create_task(self._colab_artifact_dispatch_loop(), name="stage2b-colab-artifact-dispatcher"),\n',
)

replace_once(
    p,
    '        self._tasks.clear()\n'
    '        for task in self._colab_artifact_tasks.values():\n',
    '        self._tasks.clear()\n'
    '        for task in self._colab_normal_tasks.values():\n'
    '            task.cancel()\n'
    '        if self._colab_normal_tasks:\n'
    '            await asyncio.gather(*self._colab_normal_tasks.values(), return_exceptions=True)\n'
    '        self._colab_normal_tasks.clear()\n'
    '        for task in self._colab_artifact_tasks.values():\n',
)

replace_once(
    p,
    '''                if selected_provider == "colab":
                    normal_provider = await self._select_colab_provider()
                    if not normal_provider:
                        normal_role_available = False
                        self.worker_state[target]["provider_wait"] = "No enabled/healthy Colab worker is available"
                    else:
                        self.worker_state[target]["provider_wait"] = None
                else:
                    self.worker_state[target]["provider_wait"] = None
''',
    '''                if selected_provider == "colab":
                    # Dynamic Colab workers have their own normal-work consumers.
                    # Keeping this logical role loop as a second Colab consumer
                    # would serialize the pool again and race the per-worker lanes.
                    normal_role_available = False
                    normal_provider = None
                    self.worker_state[target]["provider_wait"] = None
                else:
                    self.worker_state[target]["provider_wait"] = None
''',
)

pool_methods = r'''
    async def _claim_colab_normal_job(
        self,
        provider: str,
        targets: list[str],
        next_role_index: int = 0,
    ) -> tuple[str, dict[str, Any], int] | None:
        """Atomically claim one normal Text/Vision row for a physical Colab worker.

        ``next_runnable`` chooses the highest-priority row, while
        ``mark_processing`` is the compare-and-set claim. Multiple physical
        Colabs may observe the same pending head row; only one can flip it to
        processing. A loser immediately retries instead of sleeping for another
        scheduler poll, so an idle second Colab fills quickly.
        """
        if not targets:
            return None
        count = len(targets)
        start = int(next_role_index) % count
        for offset in range(count):
            index = (start + offset) % count
            target = targets[index]
            auto_run = self._auto_run(target)
            run_mode = "auto" if auto_run else "manual"
            for _ in range(4):
                job = await self._store.next_runnable(target, auto_run)
                if job is None:
                    break
                if not await self._store.mark_processing(
                    int(job["id"]), run_mode, str(provider)
                ):
                    continue
                claimed = dict(job)
                claimed["status"] = "processing"
                claimed["run_mode"] = run_mode
                claimed["attempt_count"] = int(claimed.get("attempt_count") or 0) + 1
                claimed["claimed_by"] = str(provider)
                claimed["_dispatch_provider"] = str(provider)
                claimed["_preclaimed_processing"] = True
                return target, claimed, (index + 1) % count
        return None

    async def _colab_normal_dispatch_loop(self) -> None:
        """Maintain one normal Stage-2B consumer per configured Colab worker."""
        while not self._stopping.is_set():
            try:
                config = self._config_getter()
                globally_stopped = self._paused("pi5") and self._paused("oneplus")
                wanted = set() if globally_stopped else set(self._configured_colab_providers())
                for provider in list(self._colab_normal_tasks):
                    task = self._colab_normal_tasks[provider]
                    if task.done() or provider not in wanted:
                        if not task.done():
                            task.cancel()
                        self._colab_normal_tasks.pop(provider, None)
                for provider in sorted(wanted):
                    task = self._colab_normal_tasks.get(provider)
                    if task is None or task.done():
                        self._colab_normal_tasks[provider] = asyncio.create_task(
                            self._colab_normal_worker_loop(provider),
                            name=f"stage2b-{provider.replace(':', '-')}-normal-worker",
                        )
                await asyncio.sleep(config.stage2b_poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stage 2B Colab normal dispatcher failed")
                await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)

    async def _colab_normal_worker_loop(self, provider: str) -> None:
        """Drain normal Text/Vision work independently on one physical Colab."""
        self._ensure_provider_state(provider)
        next_role_index = 0
        while not self._stopping.is_set():
            try:
                config = self._config_getter()
                if self._physical_worker_paused(provider):
                    await asyncio.sleep(config.stage2b_poll_interval_seconds)
                    continue

                targets = [
                    target for target in ("pi5", "oneplus")
                    if not self._paused(target) and self._selected_provider(target) == "colab"
                ]
                if not targets:
                    await asyncio.sleep(config.stage2b_poll_interval_seconds)
                    continue
                if not await self._endpoint_provider_ready(provider):
                    await asyncio.sleep(config.stage2b_poll_interval_seconds)
                    continue

                owner = f"{provider}:normal"
                if not await self._reserve_provider(provider, owner):
                    await asyncio.sleep(config.stage2b_poll_interval_seconds)
                    continue
                self.worker_state[provider]["dispatch_provider"] = provider
                claimed = None
                try:
                    claimed = await self._claim_colab_normal_job(
                        provider, targets, next_role_index
                    )
                    if claimed is not None:
                        target, job, next_role_index = claimed
                        await self._run_job(
                            target,
                            job,
                            preclaimed=True,
                            run_mode_override=str(job.get("run_mode") or "manual"),
                            state_key=provider,
                        )
                finally:
                    if provider in self.worker_state:
                        self.worker_state[provider]["dispatch_provider"] = None
                    await self._release_provider(provider, owner)

                if claimed is not None:
                    continue
                await asyncio.sleep(config.stage2b_poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stage 2B Colab normal worker %s failed", provider)
                self._events.notify("stage2b_colab_worker_error")
                await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)

'''
text = p.read_text(encoding="utf-8")
marker = '    async def _colab_artifact_dispatch_loop(self) -> None:\n'
if text.count(marker) != 1:
    raise SystemExit("Could not locate Colab artifact dispatcher insertion point")
p.write_text(text.replace(marker, pool_methods + marker, 1), encoding="utf-8")

replace_once(
    p,
    '''                    if target == "pi5" and not is_picture_route:
                        job["_active_stage"] = "pi5_text"
                        self.worker_state[state_key]["active_stage"] = "text check"
                        request, result, verdict, model, endpoint = await self._run_pi5(job)
''',
    '''                    if target == "pi5" and not is_picture_route:
                        job["_active_stage"] = "pi5_text"
                        self.worker_state[state_key]["active_stage"] = "text check"
                        if state_key == "pi5":
                            request, result, verdict, model, endpoint = await self._run_pi5(job)
                        else:
                            request, result, verdict, model, endpoint = await self._run_pi5(
                                job, role_target=state_key
                            )
''',
)

replace_once(
    p,
    '    async def _run_pi5(self, job: dict[str, Any]):\n',
    '    async def _run_pi5(self, job: dict[str, Any], role_target: str = "pi5"):\n',
)
replace_once(
    p,
    '            progress = self._prepare_oneplus_stream_region("text target reconstruction", target="pi5")\n',
    '            progress = self._prepare_oneplus_stream_region("text target reconstruction", target=role_target)\n',
)

old_version = "2026.10.03.40.11AH12"
new_version = "2026.10.04.40.11AH13"
version = Path("app/version.py")
version_text = version.read_text(encoding="utf-8")
if old_version not in version_text:
    raise SystemExit(f"Expected current version {old_version}")
version.write_text(version_text.replace(old_version, new_version), encoding="utf-8")

nav = Path("app/static/nav.js")
nav_text = nav.read_text(encoding="utf-8")
if old_version not in nav_text:
    raise SystemExit("nav.js version marker not found")
nav.write_text(nav_text.replace(old_version, new_version), encoding="utf-8")
for html in Path("app/static").glob("*.html"):
    text = html.read_text(encoding="utf-8")
    if f"?v={old_version}" in text:
        html.write_text(text.replace(f"?v={old_version}", f"?v={new_version}"), encoding="utf-8")
