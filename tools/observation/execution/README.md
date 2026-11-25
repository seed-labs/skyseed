# Adopted execution records

[中文](README.zh-CN.md)

`inspection.applied_events(plans, records)` joins saved Physical Plans to actual
Runner receipts. It returns nodes, link targets, destinations and instructions
ordered by their recorded activation time. BPF per-node generation updates and
same-time partial adoption use the receipt's instruction counts; future or
unacknowledged instructions are not treated as active.

The reader accepts restored plans and decoded execution records from JSONL or an
etcd export. It does not run models, start measurement, deploy resources or write
to Store. Instructions explain adopted state, not per-packet traversal.

Scene/layer/plan viewers remain separate Tools. This reader can inspect execution
receipts independently of the S4 manual example; it starts no traffic or capture.
