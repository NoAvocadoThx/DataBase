# Routes (FastAPI, `app/main.py`)

| path | template | who | renders |
|---|---|---|---|
| /login | login.html | public | centered name-badge card, username/password |
| / | index.html | all | 专家列表: filter grid → sortable paginated table (50/page) |
| /ask | ask.html | all | 找专家: natural-language input (amber CTA) → 系统理解 panel → ranked table with 推荐依据 |
| /expert/{id} | detail.html | all | name-badge header, 简介, 合作历史 table + add row, 修改历史, delete |
| /expert/new, /expert/{id}/edit | expert_form.html | planner+ | 2-col form |
| /documents | documents.html | planner+ | upload + documents table with status chips |
| /documents/{id} | review.html | planner+ | editable candidate cards (new=green / dup=amber), meeting fields, confirm CTA |
| /duplicates | duplicates.html | admin | A/B table per pair + merge/distinct |
| /trash | trash.html | admin | restore / purge table |
| /history | history.html | planner+ | filter grid + paginated log with old→new diffs |
| /users | users.html | admin | users table + create form |
| /account | account.html | all | profile + change password |
| /import | import.html | admin | xlsx upload |
