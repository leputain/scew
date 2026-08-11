# Public repository checklist

Репозиторий можно переводить из private в public только после выполнения всех
пунктов.

- [ ] Выбрана и добавлена подходящая `LICENSE`.
- [ ] `python scripts/public_repo_audit.py` проходит на чистом clone.
- [ ] Вся Git history проверена secret scanner, не только текущий tree.
- [ ] `.env`, dumps, logs, certificates, media exports и wheelhouse отсутствуют.
- [ ] В source/tests/docs нет production chat/user/topic IDs и private links.
- [ ] В source/tests/docs нет ФИО, usernames, email и внутренних hostname/IP.
- [ ] Все найденные ранее токены отозваны, даже если commit удалён.
- [ ] GitHub secret scanning и push protection включены.
- [ ] Branch protection требует успешный CI и запрещает force push в `main`.
- [ ] Security policy и канал private vulnerability reporting доступны.
- [ ] README не обещает функции, отсутствующие в текущем release.
- [ ] Repository description, topics и social preview не содержат внутренних данных.

После открытия доступа выполните повторный clone без credentials и проверьте
состав файлов, README links, CI badge и Docker build.
