# Finding course and assignment IDs

## Course ID (`--course` / `COURSE_ID`)

Open any page of your course on course.pku.edu.cn and look at the URL:

```text
https://course.pku.edu.cn/webapps/blackboard/execute/announcement?course_id=_98024_1
                                                                        ^^^^^^^^^
```

Copy the `_98024_1` value **including the underscores**. Set it once in `.env`
as `COURSE_ID=_98024_1` and you can omit `--course` from every command.

## Assignment ID (`--column` / gradeBookPK)

Every assignment has a numeric `gradeBookPK`. Easiest way:

```bash
uv run python main.py assignments --course _98024_1
```

```text
                     Assignments in _98024_1
┏━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ # ┃ Title             ┃ gradeBookPK ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ 1 │ 第一次作业        │      423829 │
│ 2 │ 第二次作业        │      431007 │
└───┴───────────────────┴─────────────┘

Pass the gradeBookPK value to --column, e.g. --column 423829
```

Manual alternative: open the homework list, click **查看** next to any
assignment, and read the URL of the student-list page:

```text
https://course.pku.edu.cn/webapps/bb-homeWorkCheck-BBLEARN/homeWorkCheck/getStudentWork.do?course_id=_98024_1&gradeBookPK=423829&title=第一次作业
                                                                                                        ^^^^^^^
```

The bare number after `gradeBookPK=` is your `--column`.

> `ta grade` can fetch the assignment list itself if you omit `--column`, but it
> cannot know which assignment you want, so passing `--column` is recommended.
