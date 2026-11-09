# ETF 观察台

面向 A 股和境内股票 ETF 的个人研究工具。React 19 + TypeScript 前端、Python 3.12 标准库后端，支持日内行情、日 K、季度持仓、数据覆盖检查，以及带买卖点的逐日策略回放。

## 已实现

- 研究方向按重点关注、等待机会、暂不参与、数据待补和全部五类筛选。
- 点 ETF 名称在研究页内查看报价、K 线和前十大持仓；返回清单保留筛选、搜索、滚动和焦点。
- 日线默认显示 MA5 / MA10 / MA30；策略使用的MA20/MA60等规则另行保留，修改显示不暗改交易条件。
- 全部已关注ETF提供买卖点覆盖清单：分别记录零笔、一笔、多笔、开盘取消及独立规则品种，每只都可在本页查看。
- 今年日 K 显示 B/S 收盘确认点或 B开/S开模拟执行点，可拖动交易日游标和播放历史回放。未来价格及均线留白，不参与过去的决定。
- 退出条件包含结构失效、2ATR跟踪退出、连续两日低于MA20、5日无延续及20日复核。日线确认后下一交易日开盘仅作模拟参考，开盘超出此前风险边界取消开仓。
- 每只 ETF 的季度股票前十持仓及修订版本独立保存；后台每日检查当前年和上一年，失败退避重试。
- 自动核对年内日线日期、历史暖机、缺口及复权冲突；上交所清算后份额与全篮子估算流量分开记录。
- 日K可选择近3年；后台每日增量检查日成交额与源换手率，每周复核三年完整区间。价格与量能分开存储，保留修订，不用最新份额反推历史换手。
- 三年上涨阶段复盘：逐只ETF、固定方向代表汇总、MA5/10/20/30转强时机、阶段量能与换手，以及同一MA20启动锚点后的达标/未达标对照。事后低点、峰值与原策略B/S分开。

## 本地运行

需要 Python 3.12+、Node.js 24+ 和 npm。后端不依赖第三方 Python 包。

```sh
cd server
python3 -c "import getpass; from etfmon import auth; auth.set_password('researcher', getpass.getpass('Local password: '))"
python3 -m etfmon serve --host 127.0.0.1 --port 8765
```

默认本地认证；上面的命令会交互式设置本地用户 researcher 的密码，登录时使用该账号。真实行情采集依赖可访问的公开数据源，首次补取需要时间。数据写入 `server/data/`，也可通过 `ETF_DATA` 指定目录。

另开终端：

```sh
cd web
npm ci
npm run dev
```

访问前端开发服务器打印的本机地址。Vite 将 `/api` 代理到本机后端。前端页面可见时自动更新；持续采集需让后端进程保持运行。

## 验证与构建

```sh
# 在仓库根目录；测试使用独立临时目录，不读取真实账户
ETF_DATA=/tmp/etf-tests PYTHONPATH=server python3 -m unittest discover -s server/tests -v
npm --prefix web ci
npm --prefix web run build
```

Windows 可分别设置同名环境变量再执行命令。本仓库不包含依赖目录、编译产物、历史 Git 仓库、真实行情数据、密码、会话、密钥或生产服务器配置。

## 研究口径

- 买入条件沿用固定沪深300基准及两条研究路径；准备预警不是完整买点。
- 回放年初空仓、单标的单仓位，成本为0/10/20/40基点往返假设。当前前复权数据和当前基金池存在偏差；模型损益不是实际账户收益，也未经样本外验证。
- 境内股票ETF按T+1约束模拟，不做当日卖出。跨境、黄金等品种不直接套用该模型。参见[深交所交易指南](https://investor.szse.cn/warning/activities/fundIntroduction/t20210104_584079.html)。
- 止损采用收盘确认，次日开盘参考；不模拟盘中成交、涨跌停排队、停牌、最低佣金或实际账户持仓。没有交易账户连接与自动下单。
- 持仓为季度披露，报告期、实际披露日期与采集时间分开；未知日期保持空值。来源不是实时持仓。
- 免费公开行情接口没有实时性SLA，使用者须核对来源与授权范围。历史重算不等于当时实发记录。

数据适配器位于 `server/etfmon/sources.py`、`holdings.py`、`share_history.py`；策略与回放位于 `research.py`、`replay.py`。图表使用 [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts)，组件使用 [Meta Astryx](https://github.com/meta/astryx)，依赖遵循各自许可证。

三年量能与覆盖维护位于 `history.py`，描述研究位于 `rallies.py`。如需在本地复算（只读现有数据，不发起行情请求）：

```sh
ETF_DATA=/path/to/data python3 scripts/analyze_three_years.py --out /path/to/analysis
```

输出字段级覆盖、阶段和对照CSV及JSON。详细定义见 [API口径](docs/API.md)。

需要自托管时可将构建后的静态文件放在受保护的反向代理后，后端保持回环监听。`ETF_AUTH_MODE=forward` 仅用于可信反代注入身份的已有认证部署，不能直接公开服务。
