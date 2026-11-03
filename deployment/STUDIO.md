# 研究助手接入与部署

主应用新增“研究助手”，含文献接入、论文阅读、学术对话、思维随记、自由画布、文献发现、TeX 与数据、解析与运行八个入口。写作编辑区下方也有选区评论、公式、画布与补全工具。

## 本地使用

重新启动主服务与 worker 后，数据库由 schema 15 升为 16；迁移先保存数据库备份，不改写旧迁移。前端执行 `npm ci --prefix frontend` 和 `npm run build --prefix frontend`。启动脚本接受 PowerShell Core **高于 7.6.0** 的版本。

模型任务继续使用项目当前接口、预算和费用凭证规则，不自动调用真实账号做验收。译文、对话、网页和批注均绑定当前项目；译文与布局采用不会替代人工核对。完整备份包含新对象和画布源场景，凭证文件不包含在备份里。

## Zotero 与 OpenAlex

在“文献接入 → 连接 Zotero”填写个人/群组库编号与 API Key，可分页同步所有条目和 PDF 附件。外部条目 ID 映射到稳定内部书目 ID，重复同步不替换引用目标。附件下载失败会在任务结果中逐项列出；远端删除不会自动删本地历史。

若需要 OAuth 登录，主服务环境配置 `PAPER_ZOTERO_CLIENT_KEY`、`PAPER_ZOTERO_CLIENT_SECRET`，并在 Zotero 应用中允许本机回调 `/api/v1/studio/zotero/callback`。用户拒绝授权或过期时不保存半成品连接；临时 secret 保存在本机加密凭证文件中。

OpenAlex Key 在“文献发现”的凭证区填写。直接检索不调用 AI；勾选 AI 扩展/复审会按当前模型设置计费。结果进入书目，只有 PDF 原件实际下载/解析后才有全文。

## 高级解析

- Surya 本地：安装服务依赖后运行 `uvicorn services.surya_service:app --host 127.0.0.1 --port 8769`，或启用下方 Compose 的 `ocr` profile。服务首次识别需下载模型。
- Surya Modal：安装 Modal CLI，创建名为 `yantai-surya-auth` 的 Modal Secret，内容为随机 `PAPER_PARSE_TOKEN`；部署 `services/modal_surya.py`，将返回的 HTTPS 地址与同一 token 填入研究助手。GPU/数据卷由你自己的 Modal 账号提供。
- MinerU：在界面填写 MinerU API 地址和 Key，选择模型；适配 `/api/v4/file-urls/batch` 与结果查询。第三方 API 变动需要按服务方协议调整。

高级结果先作为候选保存。点“设为当前阅读版本”后创建新的 `knowledge_parses/knowledge_units`，更新当前 head，旧批注和证据依赖会按原有机制失效。表格、公式、区域坐标、阅读顺序和置信值保留，不能把服务返回成功当成结构质量验收。

旧 DOC 导入需要本机 LibreOffice 或 `PAPER_LIBREOFFICE_PATH`。输入有二进制格式校验，转换在独立临时目录/LibreOffice 配置中执行；无引擎明确报错，不伪装成 DOCX。

## Python 执行与图片

本地运行需要 Docker 引擎。在“解析与运行”准备隔离绘图镜像，构建 `deployment/execution.Dockerfile` 并固定镜像 ID。用户代码仅在无网络、非 root、输入只读且有限额的容器运行；输出图片校验后归档为研究资源。聊天 Python 代码块也可运行和回填图像。

远端部署 `services/execution_service.py`：设置独立的 `PAPER_DATA_DIR`、至少 24 字符的随机 `PAPER_EXECUTION_TOKEN`，先构建隔离绘图镜像，再以 HTTPS 代理 `uvicorn services.execution_service:app`。该服务宿主机需要 Docker，代码仍只在子容器运行；不要把宿主 Docker socket暴露给主应用。客户端设置远端 URL 和 token，输入文件遵循当前外发权限。

## 完整主应用 Docker

部署前创建 `deployment/private/credential.key`，内容为 Fernet key，例如用可信 Python 运行 `from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())`。保存该文件并限制读取权限；它负责 Linux/macOS 上的加密凭证，丢失后需要重新填写连接凭证。

```text
docker compose -f deployment/workbench.compose.yaml up -d --build
docker compose -f deployment/workbench.compose.yaml --profile ocr up -d --build
docker compose -f deployment/workbench.compose.yaml -f deployment/workbench.gpu.yaml --profile ocr up -d --build
```

默认仅映射本机 `127.0.0.1:8765`，主应用的模型设置和工作区保存在持久卷。首次会话 bootstrap token 写入持久卷 `/data/runtime/session-8765.json`（可改 `PAPER_SESSION_DIR`）。本机打开带 `#token=...` 的地址完成认证；不是公共匿名服务。需要自定义域名时同时设置 `PAPER_ALLOWED_HOSTS` 和 `PAPER_ALLOWED_ORIGINS`，保持 HTTPS 和访问控制。云端多人服务仍用原来的独立协作网关。

## 工作区数据 CLI

```text
node scripts/yantai-data.mjs summary --project <项目ID>
node scripts/yantai-data.mjs list knowledge --project <项目ID>
node scripts/yantai-data.mjs get knowledge <文献ID> --project <项目ID>
node scripts/yantai-data.mjs search <词句> --project <项目ID>
node scripts/yantai-data.mjs summary --snapshot <完整备份.zip> --project <项目ID>
```

在线模式读取启动器会话，不回传凭证；离线模式核验备份数据库哈希并用 SQLite 只读连接。命令不修改工作区。

## 安装器和发布

`deployment/desktop/yantai.spec` 使用 PyInstaller 构建 Windows 程序/macOS app；Windows NSIS 脚本只卸载安装目录，用户的 Yantai 数据目录继续保留。macOS 必须在 macOS runner 构建 DMG；Windows OCR 在非 Windows 用 Tesseract 或云解析替代，公式/Office 功能仍需按平台核对。

GitHub 工作流包括 PR 验证、开发分支 PR 创建、Conventional Commits 版本选择、前后端版本同步、Windows/macOS 打包和 amd64/arm64 主应用镜像发布。发布为手动触发；配置存在不表示 GitHub 已执行或包已签名。正式 macOS 分发还需你自己的签名/公证配置。本次没有自动推送、发版、上传或消耗你的云账号。
