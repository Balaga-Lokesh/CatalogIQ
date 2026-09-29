"""Background jobs (requirement 2).

Submitting a job returns immediately; items are processed in the background
and job progress (done / failed / cache_hits) is updated live.

TODO: create job, run it in the background, track status
(queued -> running -> completed).
"""
