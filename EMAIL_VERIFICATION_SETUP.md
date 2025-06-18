# 📧 邮箱验证系统配置指南

## 概述

本系统实现了完整的邮箱验证注册流程，提高了账户安全性。用户注册后需要验证邮箱才能登录使用平台。

## 功能特性

- ✅ **邮箱验证注册**: 注册后发送验证邮件
- ✅ **美观邮件模板**: 专业的HTML邮件设计
- ✅ **重发验证邮件**: 支持重新发送验证链接
- ✅ **验证状态管理**: 完整的前端验证流程
- ✅ **向后兼容**: 可配置禁用验证功能
- ✅ **多邮件服务商**: 支持Gmail、QQ、163等

## 快速配置

### 1. 运行数据库迁移

```bash
# 运行迁移脚本添加邮箱验证字段
python chatpilot/apps/web/models/migrate.py
```

### 2. 配置环境变量

在 `.env` 文件中添加以下配置：

```bash
# 启用邮箱验证
EMAIL_VERIFICATION_ENABLED=true

# SMTP 服务器配置 (以Gmail为例)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_USE_TLS=true

# 发件人信息
SMTP_FROM_EMAIL=your_email@gmail.com
SMTP_FROM_NAME=Econometrics Agent

# 前端地址 (用于验证链接)
FRONTEND_URL=http://localhost:5173

# 验证链接过期时间 (小时)
EMAIL_VERIFICATION_TOKEN_EXPIRES_HOURS=24
```

### 3. 重启服务

```bash
# 重启后端服务
python -m chatpilot.main

# 重启前端服务 (如果需要)
cd web && npm run dev
```

## 主要邮件服务商配置

### Gmail 配置

```bash
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password  # 需要生成应用专用密码
SMTP_FROM_EMAIL=your_email@gmail.com
```

**注意**: Gmail 需要生成应用专用密码：
1. 开启两步验证
2. 生成应用专用密码
3. 使用应用专用密码而非普通密码

### QQ邮箱配置

```bash
SMTP_SERVER=smtp.qq.com
SMTP_PORT=587
SMTP_USERNAME=your_email@qq.com
SMTP_PASSWORD=your_authorization_code  # 需要开启SMTP服务获取授权码
SMTP_FROM_EMAIL=your_email@qq.com
```

**注意**: QQ邮箱需要：
1. 登录QQ邮箱 → 设置 → 账户
2. 开启"POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务"
3. 获取授权码并使用该授权码作为密码

### 163邮箱配置

```bash
SMTP_SERVER=smtp.163.com
SMTP_PORT=587
SMTP_USERNAME=your_email@163.com
SMTP_PASSWORD=your_client_password  # 需要开启客户端授权密码
SMTP_FROM_EMAIL=your_email@163.com
```

**注意**: 163邮箱需要：
1. 登录163邮箱 → 设置 → POP3/SMTP/IMAP
2. 开启"POP3/SMTP服务"
3. 设置客户端授权密码

## 验证流程

### 用户注册流程

1. **用户填写注册信息** → 提交表单
2. **系统创建未验证账户** → 生成验证令牌
3. **发送验证邮件** → 包含验证链接
4. **显示验证等待页面** → 提示检查邮箱
5. **用户点击验证链接** → 跳转验证页面
6. **验证成功** → 自动登录并跳转主页

### 邮件模板预览

发送的验证邮件包含：
- 美观的HTML设计
- 一键验证按钮
- 备用验证链接
- 安全提示信息
- 过期时间说明

## API端点

### 邮箱验证端点

```
POST /auths/verify-email
Body: { "token": "verification_token" }
Response: SigninResponse (自动登录)
```

### 重发验证邮件

```
POST /auths/resend-verification  
Body: { "email": "user@example.com" }
Response: { "message": "验证邮件已发送" }
```

### 检查验证状态

现有的登录端点会自动检查邮箱验证状态：

```
POST /auths/signin
Body: { "email": "user@example.com", "password": "password" }
Response: 未验证时返回错误提示
```

## 前端集成

### 注册页面

- 注册成功后显示验证等待页面
- 支持重发验证邮件功能
- 提供返回登录的选项

### 验证页面

访问路径: `/auth/verify-email?token=xxx`

功能包括：
- 自动验证Token
- 显示验证状态（成功/失败/过期）
- 重发验证邮件
- 验证成功自动跳转

## 高级配置

### 禁用邮箱验证

如需禁用邮箱验证功能（向后兼容）：

```bash
EMAIL_VERIFICATION_ENABLED=false
```

禁用后用户注册即可直接登录，无需验证邮箱。

### 自定义过期时间

```bash
# 验证链接24小时后过期 (默认)
EMAIL_VERIFICATION_TOKEN_EXPIRES_HOURS=24

# 验证链接1小时后过期
EMAIL_VERIFICATION_TOKEN_EXPIRES_HOURS=1
```

### 自定义前端地址

```bash
# 开发环境
FRONTEND_URL=http://localhost:5173

# 生产环境  
FRONTEND_URL=https://your-domain.com
```

## 故障排除

### 常见问题

**1. 邮件发送失败**
- 检查SMTP配置是否正确
- 确认邮箱服务商的SMTP服务已开启
- 验证用户名和密码/授权码

**2. 验证链接无效**
- 检查FRONTEND_URL配置是否正确
- 确认链接未过期（默认24小时）
- 验证数据库中的token是否存在

**3. 用户无法登录**
- 检查用户邮箱验证状态
- 确认EMAIL_VERIFICATION_ENABLED设置
- 查看后端日志错误信息

### 调试技巧

**查看邮件发送日志**:
```bash
# 在后端日志中搜索邮件相关信息
grep -i "email\|smtp\|verification" logs/app.log
```

**检查数据库状态**:
```python
# 检查用户验证状态
from chatpilot.apps.web.models.users import Users
user = Users.get_user_by_email("user@example.com")
print(f"Email verified: {user.email_verified}")
print(f"Verification token: {user.verification_token}")
```

**测试邮件发送**:
```python
# 测试邮件服务
from chatpilot.apps.email_utils import send_verification_email
result = send_verification_email("test@example.com", "Test User", "test-token")
print(f"Email sent: {result}")
```

## 安全考虑

- ✅ 验证令牌使用UUID4生成，安全随机
- ✅ 令牌有过期时间限制，防止长期有效
- ✅ 验证成功后清除令牌，防止重复使用
- ✅ 不泄露用户邮箱是否存在的信息
- ✅ 重发验证邮件有频率限制

## 部署注意事项

1. **生产环境配置**
   - 使用真实的SMTP服务器
   - 配置正确的FRONTEND_URL
   - 确保邮件服务的稳定性

2. **监控和日志**
   - 监控邮件发送成功率
   - 记录验证失败的情况
   - 定期清理过期的验证令牌

3. **备份和恢复**
   - 定期备份用户数据
   - 包含邮箱验证状态信息

## 总结

邮箱验证系统已成功集成到Econometrics Agent中，提供了：

- 🔐 **增强安全性**: 确保用户邮箱的真实性
- 🎨 **良好体验**: 美观的邮件模板和前端界面
- ⚙️ **灵活配置**: 支持多种邮件服务商和自定义设置
- 🔄 **向后兼容**: 可选启用，不影响现有用户

配置完成后，用户注册流程将包含邮箱验证步骤，大大提高了平台的安全性和用户账户的可靠性。