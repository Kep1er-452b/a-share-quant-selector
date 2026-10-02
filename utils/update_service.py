"""Common update application service used by CLI and Web admission layers."""
from __future__ import annotations

import time


class UpdateService:
    def execute(self, provider, target_universe, *, progress_callback=None, **options):
        started = time.perf_counter()
        phase_started, phase = started, None
        timings = {}
        def progress(payload):
            nonlocal phase_started, phase
            current = payload.get('stage', 'sync')
            now = time.perf_counter()
            if current != phase:
                if phase:
                    timings[phase] = timings.get(phase, 0) + now - phase_started
                phase, phase_started = current, now
            enriched = {**payload, 'core_elapsed_seconds': round(now - started, 3)}
            if progress_callback:
                progress_callback(enriched)
        summary = None
        try:
            summary = provider.sync_target_data(target_universe, progress_callback=progress, **options)
            return summary
        finally:
            now = time.perf_counter()
            if phase:
                timings[phase] = timings.get(phase, 0) + now - phase_started
            provider.update_timings = {**{key: round(value, 3) for key, value in timings.items()},
                                       'core_total_seconds': round(now - started, 3)}
            if isinstance(summary, dict):
                summary['core_timings'] = dict(provider.update_timings)
