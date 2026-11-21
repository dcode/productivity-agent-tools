<%*
const title = await tp.system.prompt("Meeting title");
await tp.file.move(`Meetings/${tp.date.now("YYYY/MM/YYYY-MM-DD")} ${title}`);
-%>
---
title: "<% title %>"
date: <% tp.date.now("YYYY-MM-DD") %>
start: <% tp.date.now("YYYY-MM-DDTHH:mm") %>
tags: [meeting]
---
## Prep
<!-- productivity-agent-tools:prep:start -->
<!-- productivity-agent-tools:prep:end -->

## Agenda

## Notes

## Action items
- [ ] 
