# -*- coding: utf-8 -*-
"""
Email service utility for sending verification emails
"""
import smtplib
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from typing import Optional
import urllib.parse

from loguru import logger
from chatpilot.config import (
    EMAIL_VERIFICATION_ENABLED,
    SMTP_SERVER, 
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_PASSWORD,
    SMTP_FROM_EMAIL,
    SMTP_FROM_NAME,
    FRONTEND_URL
)


class EmailService:
    """邮件服务类，用于发送各种类型的邮件"""
    
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.username = SMTP_USERNAME
        self.password = SMTP_PASSWORD
        self.from_email = SMTP_FROM_EMAIL
        self.from_name = SMTP_FROM_NAME
        self.enabled = EMAIL_VERIFICATION_ENABLED
        
    def _create_smtp_connection(self) -> Optional[smtplib.SMTP]:
        """创建SMTP连接"""
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()  # 启用安全传输
            server.login(self.username, self.password)
            return server
        except Exception as e:
            logger.error(f"Failed to create SMTP connection: {e}")
            return None
    
    def _generate_verification_token(self) -> str:
        """生成邮箱验证令牌"""
        return str(uuid.uuid4())
    
    def _create_verification_email_template(self, user_name: str, verification_url: str) -> tuple[str, str]:
        """创建邮箱验证邮件模板"""
        
        # HTML版本
        html_template = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>邮箱验证 - Econometrics Agent</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f8fafc;
        }}
        .email-container {{
            background: white;
            border-radius: 12px;
            padding: 40px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .logo {{
            font-size: 28px;
            font-weight: bold;
            color: #2563eb;
            margin-bottom: 8px;
        }}
        .subtitle {{
            color: #64748b;
            font-size: 16px;
        }}
        h1 {{
            color: #1e293b;
            font-size: 24px;
            margin-bottom: 20px;
        }}
        .verify-button {{
            display: inline-block;
            background: linear-gradient(135deg, #2563eb 0%, #3b82f6 100%);
            color: white;
            padding: 16px 32px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 16px;
            margin: 20px 0;
            transition: all 0.3s ease;
        }}
        .verify-button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 15px rgba(37, 99, 235, 0.3);
        }}
        .info-box {{
            background: #f1f5f9;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
        }}
        .warning {{
            background: #fef3c7;
            border-color: #f59e0b;
            color: #92400e;
        }}
        .footer {{
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
            color: #64748b;
            font-size: 14px;
        }}
        .link {{
            color: #2563eb;
            word-break: break-all;
        }}
    </style>
</head>
<body>
    <div class="email-container">
        <div class="header">
            <div class="logo">🏛️ Econometrics Agent</div>
            <div class="subtitle">专业经济计量分析平台</div>
        </div>
        
        <h1>欢迎使用 Econometrics Agent！</h1>
        
        <p>你好 <strong>{user_name}</strong>，</p>
        
        <p>感谢你注册我们的经济计量分析平台！为了确保账户安全，请点击下面的按钮验证你的邮箱地址：</p>
        
        <div style="text-align: center;">
            <a href="{verification_url}" class="verify-button">
                🔐 验证邮箱地址
            </a>
        </div>
        
        <div class="info-box">
            <strong>📋 验证步骤：</strong>
            <ol>
                <li>点击上方"验证邮箱地址"按钮</li>
                <li>在浏览器中完成验证</li>
                <li>开始使用完整的平台功能</li>
            </ol>
        </div>
        
        <div class="info-box warning">
            <strong>⚠️ 安全提示：</strong>
            <ul>
                <li>验证链接将在 <strong>24小时</strong> 后过期</li>
                <li>如果你没有注册我们的服务，请忽略此邮件</li>
                <li>不要将此链接分享给他人</li>
            </ul>
        </div>
        
        <p>如果按钮无法点击，请复制以下链接到浏览器中打开：</p>
        <p class="link">{verification_url}</p>
        
        <div class="footer">
            <p>此邮件由 Econometrics Agent 系统自动发送，请勿直接回复。</p>
            <p>如有疑问，请联系我们的技术支持团队。</p>
        </div>
    </div>
</body>
</html>
        """
        
        # 纯文本版本
        text_template = f"""
🏛️ Econometrics Agent - 邮箱验证

你好 {user_name}，

感谢你注册我们的经济计量分析平台！

为了确保账户安全，请访问以下链接验证你的邮箱地址：

{verification_url}

验证步骤：
1. 点击或复制上述链接到浏览器
2. 在浏览器中完成验证
3. 开始使用完整的平台功能

安全提示：
- 验证链接将在 24小时 后过期
- 如果你没有注册我们的服务，请忽略此邮件
- 不要将此链接分享给他人

---
此邮件由 Econometrics Agent 系统自动发送，请勿直接回复。
如有疑问，请联系我们的技术支持团队。
        """
        
        return html_template, text_template
    
    def send_verification_email(self, user_email: str, user_name: str, verification_token: str) -> bool:
        """发送邮箱验证邮件"""
        if not self.enabled:
            logger.info("Email verification is disabled, skipping email send")
            return True
            
        try:
            # 构建验证URL
            verification_url = f"{FRONTEND_URL}/auth/verify-email?token={verification_token}"
            
            # 创建邮件内容
            html_content, text_content = self._create_verification_email_template(user_name, verification_url)
            
            # 创建邮件消息
            msg = MIMEMultipart('alternative')
            msg['From'] = formataddr((self.from_name, self.from_email))
            msg['To'] = user_email
            msg['Subject'] = f"[{self.from_name}] 请验证你的邮箱地址"
            
            # 添加文本和HTML部分
            text_part = MIMEText(text_content, 'plain', 'utf-8')
            html_part = MIMEText(html_content, 'html', 'utf-8')
            
            msg.attach(text_part)
            msg.attach(html_part)
            
            # 发送邮件
            server = self._create_smtp_connection()
            if server:
                try:
                    server.send_message(msg)
                    logger.info(f"Verification email sent successfully to {user_email}")
                    return True
                finally:
                    server.quit()
            else:
                logger.error("Failed to create SMTP connection")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send verification email to {user_email}: {e}")
            return False
    
    def send_welcome_email(self, user_email: str, user_name: str) -> bool:
        """发送欢迎邮件（验证成功后）"""
        if not self.enabled:
            return True
            
        try:
            html_content = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .welcome-container {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 12px;
            padding: 40px;
            text-align: center;
        }}
        h1 {{ color: white; margin-bottom: 20px; }}
        .features {{
            background: white;
            color: #333;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
        }}
    </style>
</head>
<body>
    <div class="welcome-container">
        <h1>🎉 欢迎加入 Econometrics Agent！</h1>
        <p>你好 <strong>{user_name}</strong>，</p>
        <p>你的邮箱已验证成功！现在可以使用平台的所有功能了。</p>
    </div>
    
    <div class="features">
        <h3>🚀 你现在可以：</h3>
        <ul>
            <li>📊 上传数据集进行经济计量分析</li>
            <li>🤖 使用AI助手解释统计结果</li>
            <li>📈 生成专业的统计报告</li>
            <li>📚 学习经济计量方法</li>
        </ul>
    </div>
    
    <p style="text-align: center; margin-top: 30px;">
        <a href="{FRONTEND_URL}" 
           style="background: #2563eb; color: white; padding: 12px 24px; 
                  text-decoration: none; border-radius: 6px; font-weight: 600;">
            开始使用平台
        </a>
    </p>
</body>
</html>
            """
            
            text_content = f"""
🎉 欢迎加入 Econometrics Agent！

你好 {user_name}，

你的邮箱已验证成功！现在可以使用平台的所有功能了。

你现在可以：
- 📊 上传数据集进行经济计量分析
- 🤖 使用AI助手解释统计结果  
- 📈 生成专业的统计报告
- 📚 学习经济计量方法

访问平台：{FRONTEND_URL}
            """
            
            msg = MIMEMultipart('alternative')
            msg['From'] = formataddr((self.from_name, self.from_email))
            msg['To'] = user_email
            msg['Subject'] = f"[{self.from_name}] 欢迎使用我们的平台！"
            
            msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            server = self._create_smtp_connection()
            if server:
                try:
                    server.send_message(msg)
                    logger.info(f"Welcome email sent successfully to {user_email}")
                    return True
                finally:
                    server.quit()
            else:
                return False
                
        except Exception as e:
            logger.error(f"Failed to send welcome email to {user_email}: {e}")
            return False


# 创建全局邮件服务实例
email_service = EmailService()


def send_verification_email(user_email: str, user_name: str, verification_token: str) -> bool:
    """发送邮箱验证邮件的便捷函数"""
    return email_service.send_verification_email(user_email, user_name, verification_token)


def send_welcome_email(user_email: str, user_name: str) -> bool:
    """发送欢迎邮件的便捷函数"""
    return email_service.send_welcome_email(user_email, user_name)


def generate_verification_token() -> str:
    """生成验证令牌的便捷函数"""
    return str(uuid.uuid4())