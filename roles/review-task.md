Review the branch {{BRANCH}} at HEAD {{HEAD_SHA}}, worktree {{WT}}, base {{BASE}}.
Check it by your reviewer rules above: diff, tests, linter, build, OpenSpec (if present). Review exactly {{HEAD_SHA}}; if the branch moves while you work, say so in the report.
Write the report to {{REPORT}}. Its last line is the verdict, exactly:
`{{BRANCH}}@{{HEAD_SHA}}: APPROVED — <short>` or `{{BRANCH}}@{{HEAD_SHA}}: CHANGES REQUESTED — <short>`
