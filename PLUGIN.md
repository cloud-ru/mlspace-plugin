# Как устроен плагин

Для обычной работы достаточно [установки](INSTALL.md).
Эта страница объясняет, что устанавливается и как приходят обновления.

## Одна поставка

```text
src/mlspace_mcp/   MCP-сервер и схемы API
skills/           инструкции агенту для типовых задач
.claude-plugin/   подключение к Claude Code
.codex-plugin/    подключение к Codex
plugin.json       описание плагина
mcp.json          команда запуска MCP
pyproject.toml    Python-зависимости и команда mlspace-plugin
```

MCP-сервер выполняет обращения к API MLSpace. Скиллы помогают агенту выбирать
действия и проводить пользователя через задачу. Мастер `setup` входит в сервер.

Свой пакет MLSpace на PyPI публиковать не нужно. Нативный клиент получает
исходники сервера вместе со скиллами, а `uv` подготавливает Python и сторонние
зависимости. Ключи и сертификаты хранятся отдельно от плагина.

## Как подключаются клиенты

| Клиент | Что делает `setup` |
| --- | --- |
| Claude Code | Добавляет каталог плагинов этого репозитория и устанавливает `mlspace@mlspace` |
| Codex | Добавляет каталог плагинов этого репозитория и устанавливает `mlspace@mlspace` |
| OpenCode | Записывает команду запуска MCP и устанавливает скиллы в каталог OpenCode |

В Claude Code и Codex каталог плагинов называется *marketplace*.
Здесь это файл в том же Git-репозитории, а не отдельный магазин или сервис.
После настройки клиент запускает MCP как локальный процесс через stdio.

## Какие скиллы входят

| Сценарий | Скиллы |
| --- | --- |
| Настроить доступ | [Настройка MLSpace](skills/mlspace-setup/SKILL.md) |
| Подготовить обучение | [Выбор ресурсов](skills/mlspace-discover-launchable-resources/SKILL.md), [запуск задачи](skills/mlspace-launch-training-job/SKILL.md) |
| Проверить обучение | [Запущенные задачи](skills/mlspace-list-running-jobs/SKILL.md), [отказ запуска](skills/mlspace-diagnose-job-launch-rejection/SKILL.md), [сбой задачи](skills/mlspace-diagnose-failed-training-job/SKILL.md) |
| Работать с ноутбуком | [Возобновление и автоотключение](skills/mlspace-resume-notebook-and-set-autoshutdown/SKILL.md) |
| Работать с инференсом | [Развёртывание](skills/mlspace-deploy-inference-service/SKILL.md), [вызов модели](skills/mlspace-call-deployed-model-predict/SKILL.md), [диагностика](skills/mlspace-diagnose-unhealthy-inference/SKILL.md) |
| Перенести вычислительный узел | [Перенос между очередями](skills/mlspace-transfer-compute-node/SKILL.md) |
| Настроить перенос данных | [Выбор маршрута и расписания](skills/mlspace-transfer-data/SKILL.md) |
| Перенести данные между хранилищами | [S3 → NFS](skills/mlspace-stage-s3-to-nfs/SKILL.md), [NFS → NFS](skills/mlspace-transfer-nfs-to-nfs/SKILL.md), [NFS → S3](skills/mlspace-export-nfs-to-s3/SKILL.md), [S3 → S3](skills/mlspace-migrate-s3-to-s3/SKILL.md) |
| Выгрузить базу данных | [БД → хранилище](skills/mlspace-export-database-data/SKILL.md) |
| Разобраться с переносом | [Диагностика Data Transfer](skills/mlspace-diagnose-data-transfer/SKILL.md) |

Конкретные маршруты переноса зависят от API, коннекторов и прав пользователя.
Наличие типа среди источников не означает, что он доступен как назначение.

## Обновления

- **Claude Code и Codex:** менеджер клиента обновляет плагин целиком — сервер и скиллы.
- **OpenCode:** повторный `setup` обновляет команду MCP и пакетные скиллы вместе.
  При установке из Git команда сервера привязывается к конкретному коммиту.
- **Локальная установка через `uv tool install`:** сначала установите новую копию,
  затем выполните `mlspace-plugin setup --force`.

[Команда обновления для пользователя](INSTALL.md#обновление-и-повторная-настройка).
Плагин не запускает собственное фоновое обновление и не меняет настройки
автообновления клиентов. Удалённые подключения сами не возвращаются.

Ключи остаются на месте. Дополнительные пользовательские скиллы OpenCode
сохраняются; для изменённых пакетных скиллов мастер предлагает выбор.
Не храните свои правки в кеше нативного плагина — обновление заменяет этот каталог.

Для сопровождающих: [как собрать и выпустить новую версию](RELEASE.md).
