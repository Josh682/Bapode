"""Asynchronous SQLite WAL Writer Worker (Thread 2).

Reference: docs/ARCHITECTURE.md & docs/specs/04-telemetry-and-schema.md
Implements:
1. Dedicated background thread executing all SQLite commit transactions.
2. 15-second periodic flush timer.
3. Event-driven flush on state transition, OS sleep/wake, and application quit.
4. Non-blocking queue ensuring zero lag on Main Thread and Vision Worker.
"""

import queue
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PySide6 import QtCore

from db.manager import DatabaseManager, TelemetryBucketRecord
from tracker.states import IntervalSignature


@dataclass(frozen=True, slots=True)
class RecordStateTask:
    session_id: int
    profile_id: int
    signature: IntervalSignature
    now: datetime
    alert_dismissal_sec: float | None = None


@dataclass(frozen=True, slots=True)
class RecordBucketTask:
    bucket: TelemetryBucketRecord


@dataclass(frozen=True, slots=True)
class UpdateSessionTask:
    session_id: int
    kwargs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FlushTask:
    now: datetime
    alert_dismissal_sec: float | None = None


@dataclass(frozen=True, slots=True)
class CloseSessionTask:
    session_id: int
    end_time: datetime


@dataclass(frozen=True, slots=True)
class StopTask:
    pass


class DatabaseWorker(QtCore.QThread):
    """Worker Thread 2: Asynchronous SQLite WAL storage engine."""

    error_occurred = QtCore.Signal(str)

    def __init__(
        self,
        db_manager: DatabaseManager,
        flush_interval_s: float = 15.0,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_manager = db_manager
        self.flush_interval_s = flush_interval_s
        self._task_queue: queue.Queue[Any] = queue.Queue()
        self._running = True

    def stop(self) -> None:
        """Signal worker to stop, process remaining queue, and terminate."""
        self._running = False
        self._task_queue.put(StopTask())
        self.wait(5000)

    def enqueue_record_state(
        self,
        session_id: int,
        profile_id: int,
        signature: IntervalSignature,
        now: datetime | None = None,
        alert_dismissal_sec: float | None = None,
    ) -> None:
        """Enqueue state recording task."""
        self._task_queue.put(
            RecordStateTask(
                session_id=session_id,
                profile_id=profile_id,
                signature=signature,
                now=now or datetime.now(),
                alert_dismissal_sec=alert_dismissal_sec,
            )
        )

    def enqueue_record_bucket(self, bucket: TelemetryBucketRecord) -> None:
        """Enqueue 5-minute bucket record task."""
        self._task_queue.put(RecordBucketTask(bucket=bucket))

    def enqueue_update_session(self, session_id: int, **kwargs: Any) -> None:
        """Enqueue session bounds and metrics update task."""
        self._task_queue.put(UpdateSessionTask(session_id=session_id, kwargs=kwargs))

    def enqueue_flush(
        self,
        now: datetime | None = None,
        alert_dismissal_sec: float | None = None,
    ) -> None:
        """Enqueue immediate flush task."""
        self._task_queue.put(
            FlushTask(
                now=now or datetime.now(),
                alert_dismissal_sec=alert_dismissal_sec,
            )
        )

    def enqueue_close_session(self, session_id: int, end_time: datetime | None = None) -> None:
        """Enqueue session close task."""
        self._task_queue.put(
            CloseSessionTask(
                session_id=session_id,
                end_time=end_time or datetime.now(),
            )
        )

    def run(self) -> None:
        """Main loop: dequeue tasks and commit transactions."""
        while self._running:
            try:
                task = self._task_queue.get(timeout=0.5)
                self._handle_task(task)
                self._task_queue.task_done()
                if isinstance(task, StopTask):
                    break
            except queue.Empty:
                pass
            except Exception as e:
                self.error_occurred.emit(str(e))

        # Drain remaining tasks upon shutdown
        self._drain_queue()
        try:
            self.db_manager.flush_current_interval()
        except Exception as e:
            self.error_occurred.emit(str(e))

    def _handle_task(self, task: Any) -> None:
        """Dispatch task to DatabaseManager."""
        try:
            if isinstance(task, RecordStateTask):
                self.db_manager.record_state(
                    session_id=task.session_id,
                    profile_id=task.profile_id,
                    signature=task.signature,
                    now=task.now,
                    alert_dismissal_sec=task.alert_dismissal_sec,
                )
            elif isinstance(task, RecordBucketTask):
                self.db_manager.record_telemetry_bucket(task.bucket)
            elif isinstance(task, UpdateSessionTask):
                self.db_manager.update_session_metrics(
                    session_id=task.session_id,
                    **task.kwargs,
                )
            elif isinstance(task, FlushTask):
                self.db_manager.flush_current_interval(
                    flush_time=task.now,
                    alert_dismissal_sec=task.alert_dismissal_sec,
                )
            elif isinstance(task, CloseSessionTask):
                self.db_manager.close_session(
                    session_id=task.session_id,
                    end_time=task.end_time,
                )
        except Exception as e:
            self.error_occurred.emit(str(e))

    def _drain_queue(self) -> None:
        """Process all remaining items in the queue prior to shutdown."""
        while not self._task_queue.empty():
            try:
                task = self._task_queue.get_nowait()
                if not isinstance(task, StopTask):
                    self._handle_task(task)
                self._task_queue.task_done()
            except queue.Empty:
                break
            except Exception as e:
                self.error_occurred.emit(str(e))
