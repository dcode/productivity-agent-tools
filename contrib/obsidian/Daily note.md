---
tags: [daily]
---
# {{date:dddd, MMMM D, YYYY}}

## Plan
<!-- productivity-agent-tools:plan:start -->
<!-- productivity-agent-tools:plan:end -->

## Scheduled today
```dataview
TABLE status, priority, due
FROM #task
WHERE scheduled AND dateformat(date(scheduled), "yyyy-MM-dd") = this.file.name
SORT priority DESC
```

## Notes

## Wrap-up
<!-- productivity-agent-tools:wrap-up:start -->
<!-- productivity-agent-tools:wrap-up:end -->
