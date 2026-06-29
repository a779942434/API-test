---
name: qwen
description: 通过 AgentChat CDP 浏览器转发消息给 Qwen Chat (https://chat.qwen.ai/)，获取 AI 方案后回传。用于复杂问题的第二意见、方案评审、Prompt 优化建议等场景。
---

# Qwen AgentChat 转发技能

## 前置条件

本技能依赖后台运行的 AgentChat 服务，该服务通过 Chrome DevTools Protocol (CDP) 控制 Qwen Chat 浏览器页面。

### 启动 AgentChat（由用户操作）

```bash
# AgentChat 服务会启动 Chrome 并打开 https://chat.qwen.ai/
# CDP 端点: http://127.0.0.1:9222
```

### 健康检查

每次调用时，首先检查 CDP 端点是否可用：

```bash
curl -s http://127.0.0.1:9222/json | python3 -c "import sys,json; pages=json.load(sys.stdin); qwen=[p for p in pages if 'chat.qwen.ai' in p.get('url','')]; print('OK' if qwen else 'NO_PAGE')"
```

## 分工哲学（核心）

| 角色 | 负责 | 优势 |
|------|------|------|
| **Claude (我)** | 代码阅读、分析定位、写入文件、执行命令 | 直接访问代码库和文件系统 |
| **Qwen** | 方案设计、算法思路、Prompt 优化、第二意见 | 浏览器免费、不同模型视角 |
| **DeepSeek API** | 大批量测试用例生成、结构化 JSON 输出 | 速度快、token 上限高、已集成 |

### 典型工作流

1. **Claude** 分析代码 → 整理问题 + 上下文 → 发送给 Qwen
2. **Qwen** 输出方案/建议 → **Claude** 提取回复
3. **Claude** 实施方案 → 写代码 → 验证
4. （可选）复杂逻辑让 **Qwen** review → **Claude** 采纳修改

## 使用方法

### 基本调用：`/qwen <你的问题>`

我会：
1. 检查 AgentChat 是否在线
2. 如果在线 → 通过 Playwright 在 Qwen 输入框中键入消息 → 提交 → 等待回复 → 提取内容 → 展示给你
3. 如果不在线 → 提示 "AgentChat 未启动，请先启动 AgentChat 服务 (CDP: http://127.0.0.1:9222)"

### 带代码上下文：`/qwen 帮我分析这段代码的问题`

我会自动附上相关文件内容作为上下文。

### 方案评审：`/qwen review 上面的方案有什么遗漏`

我会把当前讨论的方案 + Qwen 之前的回复一起发给它。

## 交互细节

### 发送消息

1. 使用 `mcp__plugin_playwright_playwright__browser_navigate` 导航到 `https://chat.qwen.ai/`
2. 使用 `mcp__plugin_playwright_playwright__browser_snapshot` 确认页面加载
3. 使用 `mcp__plugin_playwright_playwright__browser_type` 填入消息到输入框 `textbox "有什么我能帮您的吗？"`
4. 使用 `mcp__plugin_playwright_playwright__browser_press_key` 按 Enter 提交
5. 等待回复（循环 `browser_wait_for` + `browser_snapshot`，每次 10-15 秒）
6. 使用 `mcp__plugin_playwright_playwright__browser_run_code_unsafe` 提取回复文本：
   ```js
   async (page) => {
     return await page.evaluate(() => {
       const main = document.querySelector('main');
       return main ? main.innerText : '';
     });
   }
   ```

### 判断回复完成

- Qwen 侧边栏会显示思考状态（如 "重构提示结构..."）
- 当出现 "已经完成思考" 且后续出现标题/正文时，回复完成
- 如果超过 120 秒无新内容，假定已完成

### 提取回复

使用 `browser_run_code_unsafe` 中的 `page.evaluate` 提取 `main` 元素的 `innerText`，从用户消息之后的内容截取。

## 降级策略

```
检查 CDP 端点
  ├── ✅ 可用 + Qwen 页面存在 → 正常发送
  ├── ⚠️ 可用但无 Qwen 页面 → 导航到 https://chat.qwen.ai/ 后再发送
  ├── ❌ 不可用 → 提示用户启动 AgentChat，本次使用 DeepSeek API 替代
  └── ⏱️ 超时无回复 → 提示 "Qwen 响应超时，可能是网络问题或消息过长"
```

## 已知限制

1. **速度较慢**：Qwen3.7-Plus 深度思考模式可能耗时 2-5 分钟
2. **输出截断**：浏览器显示的超长回复可能被截断，需分段提取
3. **纯文本**：目前只提取文本，代码块中的格式可能丢失
4. **单会话**：所有消息在同一个 Qwen 对话中，需手动管理上下文

## 适用场景 vs 不适用场景

**✅ 适用**：
- 复杂架构方案评审
- Prompt 优化建议
- 算法/逻辑设计
- 第二意见验证
- 代码审查建议

**❌ 不适用**（用 DeepSeek API 或 Claude 直接处理）：
- 大批量 JSON 生成
- 需要读写文件的代码修改
- 毫秒级响应的简单问答
- 需要执行/验证的代码
