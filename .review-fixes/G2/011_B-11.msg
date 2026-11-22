fix(task-center): expire unread notifications so cleanup can progress

Cleanup only removed read notifications, and task cleanup skipped tasks
with a notification entirely, also when counting the max_terminal_tasks cap.
An inbox the user never opens kept tasks, events and notifications growing
without bound. Unread notifications now expire after max(3 x retention, 90)
days and are capped like read ones, and every terminal task counts towards
the cap (linked tasks are still kept while their notification exists).

Refs: B-11
