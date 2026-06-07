# 每日一讲 · Daily Speech

## 项目简介

「每日一讲」是一个自动化的中文演讲稿生成与发布项目。每天北京时间 06:30,GitHub Actions 会调用大模型(默认 DeepSeek,可换其他 OpenAI 兼容服务)生成一篇 5 分钟左右、1200-1600 字的中文演讲稿,自动提交到仓库,并通过 GitHub Pages 部署到 https://Jmiles129250.github.io/daily-speech/。脚本包含选题、写作、长度校验、索引构建等完整流程,你可以直接 fork 之后接入自己的 API Key,就能拥有属于自己的「每日演讲」。

## 启用步骤

1. 打开 **Settings → Pages → Build and deployment**,把 **Source** 选为 **GitHub Actions**(不是 Deploy from a branch)。
2. 打开 **Settings → Secrets and variables → Actions → New repository secret**,按需添加以下密钥:
   - **`github`** —— 你的大模型 API Key（在 Secrets 里 secret 名是 `github`，脚本会从这里读）。
   - **`LLM_API_BASE`**（可选）—— 你的 API Base URL。不填默认 `https://api.MiniMax.chat/v1`。
   - `LLM_MODEL`（可选）—— 默认 `MiniMax-M2.5`，你也可以在 Variables 里加一个 `LLM_MODEL` 覆盖。

   > 不需要再额外配 `GH_TOKEN`：workflow 已经使用 Actions 自动颁发的 `GITHUB_TOKEN`，工作流对仓库有 `contents: write` / `pages: write` / `id-token: write` 权限，开箱即用。

3. （可选）**微信推送**：每天演讲稿生成完毕后往你的微信推一条。脚本同时支持 Server 酱（推荐，个人微信直接收）和企业微信群机器人，二选一即可。
   - **方案 A — Server 酱（推荐，5 分钟搞定）**
     1. 打开 https://sct.ftqq.com/ 用微信扫码登录
     2. 微信扫码绑定「方糖」服务号
     3. 复制 **SendKey**（形如 `SCT...`）
     4. 回到 GitHub repo → Settings → Secrets → New secret：
        - Name: `WECHAT_SENDKEY`
        - Secret: 粘上你的 SendKey
   - **方案 B — 企业微信群机器人**
     1. 在企业微信里建一个群（或用现有群）
     2. 群设置 → 群机器人 → 添加 → 拿到一个 webhook URL（形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...`）
     3. 回到 GitHub repo → Settings → Secrets → New secret：
        - Name: `WECHAT_WEBHOOK`
        - Secret: 粘上完整 webhook URL
   - 两个都填也能跑，Server 酱会优先。
   - 都不填也能跑，推送步骤会跳过，不影响生成和部署。
3. 推送到 `main` 之后,工作流会在每天 **北京时间 06:30**(UTC 22:30)自动运行;你也可以在 **Actions → Generate and Deploy Daily Speech → Run workflow** 里手动触发一次,立即生成今天的演讲稿并部署。

## 访问地址

正式站点: **https://Jmiles129250.github.io/daily-speech/**

## 文件结构

```
.
├── .github/workflows/daily.yml   # 每日生成 + 部署工作流
├── scripts/
│   ├── generate_speech.py        # 调用 LLM 生成今日演讲稿(幂等)
│   ├── build_index.py            # 扫描 speeches/*.md,生成 index.json / manifest.json
│   └── count_cjk.py              # 调试用:统计 .md 文件里的汉字数
├── speeches/
│   ├── YYYY-MM-DD.md             # 每天的演讲稿,带 YAML frontmatter
│   ├── index.json                # 按日期倒序的索引(后端自用)
│   └── manifest.json             # 前端 fetch 的清单,结构与 index.json 相同
├── requirements.txt
├── .gitignore
└── README.md
```

## 工作流概览

每天 06:30(北京时间)流程如下:

1. **Checkout** 仓库源码。
2. **Setup Python 3.11** 并 `pip install -r requirements.txt`。
3. **生成演讲稿**:`python scripts/generate_speech.py`
   - 计算今天日期(Asia/Shanghai)。
   - 如果 `speeches/YYYY-MM-DD.md` 已存在,直接退出(幂等)。
   - 读取 `speeches/index.json`,把已用过的标题作为「禁用标题」喂给 LLM。
   - 用中文系统 prompt 调用 OpenAI 兼容的 chat completions 接口,温度 0.9。
   - 剥掉 `<think>...</think>` 之类的思考块,校验正文字数(1200-1600 汉字),偏短或偏长会自动重试一次,带长度修正指令。
   - 从首行 `《...》`、或第一个 `# 标题`、或正文前 20 字回退提取标题。
   - 写入 `speeches/YYYY-MM-DD.md`,并以「读 → 追加 → 写临时文件 → rename」的方式原子地更新 `speeches/index.json`。
4. **构建索引**:`python scripts/build_index.py`
   - 遍历 `speeches/*.md`,解析 `---` frontmatter(无需 PyYAML)。
   - 抽取日期、标题、文件路径,并截取正文前 80 字符作为摘要。
   - 写入 `speeches/index.json` 和 `speeches/manifest.json`(同结构,后者给前端)。
5. **自动提交**:`stefanzweifel/git-auto-commit-action` 提交 `speeches/**`。
6. **部署**:`actions/deploy-pages` 把整个仓库作为静态站点部署到 GitHub Pages。

## 当前默认配置

- **模型**:`MiniMax-M2.5`(可在 repo 的 Variables 里加 `LLM_MODEL` 覆盖)
- **Base URL**:`https://api.minimaxi.com/v1`(MiniMax 国内端点,可在 Secrets 里加 `LLM_API_BASE` 覆盖;国际版用 `https://api.minimax.chat/v1`)
- **API 路径**:`/text/chatcompletion_v2`(MiniMax 专有路径,默认就是;可加 `LLM_API_PATH` 覆盖)
- **API Key**:从 Secrets 的 `github` 项读取

## 推送样例（Server 酱 / 企业微信）

每天演讲稿生成并部署完成后，会推送一条消息，类似：

> **每日演讲 · 人生的加分项**
> 1997年夏天，安徽一个小村庄里，一个十八岁的少年蹲在田埂上……
> 👉 [打开每日演讲](https://Jmiles129250.github.io/daily-speech/)

企业微信会渲染为 Markdown 卡片，Server 酱会发到「方糖」公众号，再转发到你的个人微信。

## 常见问题

- **LLM 调用失败 / 401 Unauthorized**
  - 检查 `LLM_API_KEY` 是否正确,以及 `LLM_API_BASE` 是否填的是 `/v1` 结尾的根 URL(脚本会自动拼 `/chat/completions`)。
  - 如果是 Azure 或其他代理,可能需要把 `LLM_API_BASE` 改成服务商给的完整 endpoint。
- **长度校验一直不过 / 重试后仍超长**
  - 极端情况下 LLM 可能反复偏短或偏长,脚本会接受「最接近区间」的一版,确保当天的内容仍能产出,而不是让站点空着。
  - 想让窗口更宽松可以编辑 `scripts/generate_speech.py` 顶部的 `MIN_CHARS` / `MAX_CHARS`。
- **自动提交失败 / 403 Permission denied**
  - 默认用 Actions 颁发的 `GITHUB_TOKEN`，对仓库有 contents:write 权限，不需要额外配置。
- **Pages 部署看不到新内容**
  - 第一次启用需要先在 **Settings → Pages** 把 Source 切到 **GitHub Actions**,之后才会创建 `github-pages` 环境。
  - 浏览器可能缓存,Ctrl/Cmd + Shift + R 强刷一下,或者等几分钟让 CDN 生效。
- **想换 LLM / 改 prompt**
  - LLM 服务商:在环境变量里覆盖 `LLM_API_BASE`、`LLM_MODEL` 即可,无需改代码。
  - 写作风格:编辑 `scripts/generate_speech.py` 顶部的 `SYSTEM_PROMPT`,下次生成即生效。
- **想用本地时间而不是北京时间**
  - 把 `BEIJING_TZ` 改成 `timezone(timedelta(hours=...))`,或者在生成日期前 `os.environ["TZ"] = "..."` 并 `time.tzset()`。

## 本地开发

```bash
# 一次性安装依赖
pip install -r requirements.txt

# 手工生成一篇(需要先在环境里注入 LLM_API_KEY)
LLM_API_KEY=sk-... python scripts/generate_speech.py
python scripts/build_index.py
```

## License

MIT.
