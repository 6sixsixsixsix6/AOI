# AOI 工作区分类

## generation：虚假环境生成
- generator.py：生成真实世界和虚假世界描述
- renderer.py：渲染环境描述
- vulnerability_renderer.py：渲染漏洞信息
- fake_world.json：已生成的虚假世界
- real_world.json：真实世界基线

## injection：虚假信息注入
- manipulations.py：修改漏洞状态和注入字段
- outputs/：API 输出、漏洞描述和渲染结果

## automation：自动化实验
- pipeline.py：实验流程编排
- run_repeatable_experiment.sh：Baseline/Injected 自动攻击、记录、恢复容器

## validation：验证和查看
- validator.py：验证环境内容
- show_world.py：查看真实环境和虚假环境

## experiments：实验结果
- runs/：每轮攻击结果
- checkpoint_20260830/：环境快照和历史世界文件

## 当前已完成
- Baseline 自动攻击
- Injected 自动攻击
- Qwen 自动选择漏洞字段
- qwen3.8-max API 接入
- 攻击 token 单独统计
- 攻击后自动重建干净容器
- 实验报告和归档导出

## 当前尚未完成
- fake_version 接入实际页面或响应头
- Fake Page 页面注入
- 虚假 CVE、资产、凭据、Flag、攻击路径
- Policy Manipulation
- 多种虚假环境的批量实验对比
