# ld-scrapling

政府采购公告爬虫，监控云南中烟和天津市政府采购网，按关键词过滤后保存本地并推送飞书通知。

## 项目简介

两个独立的单文件爬虫：

- **ynzy_scrapling.py** — 云南中烟招标公告（`www.ynzy-tobacco.com`）
- **tjgp_scrapling.py** — 天津市政府采购网（`tjgp.cz.tj.gov.cn`）

命中关键词的公告会保存到 `data/`，并通过 `feishu_push.py` 推送到飞书群。所有配置在 `config.json`，**支持热重载**——运行中改完下个周期就生效，不用重启。

## 本地开发

```powershell
# 激活虚拟环境
.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行（两个爬虫并发）
python main.py
```

## 服务器部署（Docker）

### 1. 上传文件

通过 SFTP 将以下文件上传到服务器目录（如 `/opt/server/ld-scrapling/`）：

```
main.py
ynzy_scrapling.py
tjgp_scrapling.py
feishu_push.py
config.json
requirements.txt
Dockerfile
docker-compose.yml
.dockerignore
```

不需要上传：`.venv/`、`data/`、`log/`、`__pycache__/`、`feishu_config.json`

### 2. 在服务器上创建 feishu_config.json

```bash
cd /opt/server/ld-scrapling

cat > feishu_config.json << 'EOF'
{
  "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/你的webhook",
  "secret": "你的secret"
}
EOF
```

没有飞书推送需求可跳过此步，爬虫会静默跳过推送。

### 3. 构建并启动

```bash
docker compose up -d --build
```

### 4. 确认运行

```bash
# 查看容器状态
docker compose ps

# 实时查看日志
docker compose logs -f
```

## 日常操作

```bash
# 修改关键词、日期等配置（热重载，无需重启容器）
vi config.json

# 更新代码后重新构建
docker compose up -d --build

# 重新构建后清理残留的悬空镜像（<none> 标签，安全，不会动到运行中的镜像）
docker image prune -f

# 停止
docker compose down

# 查看采集结果
ls data/天津市政府采购信息/
ls data/云南中烟招标公告信息/
```
