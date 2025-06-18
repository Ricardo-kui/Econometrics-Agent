<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import { verifyEmail, resendVerificationEmail } from '$lib/apis/auths';
	import toast from 'svelte-french-toast';

	let verificationStatus: 'verifying' | 'success' | 'error' | 'expired' = 'verifying';
	let errorMessage = '';
	let userEmail = '';
	let isResending = false;
	let canResend = true;
	let resendCountdown = 0;

	// 从 URL 参数获取 token
	$: token = $page.url.searchParams.get('token');

	onMount(async () => {
		if (!token) {
			verificationStatus = 'error';
			errorMessage = '无效的验证链接';
			return;
		}

		await handleVerification();
	});

	async function handleVerification() {
		try {
			verificationStatus = 'verifying';
			
			const response = await verifyEmail(token);
			
			if (response && response.token) {
				// 验证成功，设置用户信息和令牌
				localStorage.setItem('token', response.token);
				
				// 更新用户状态
				user.set({
					id: response.id,
					email: response.email,
					name: response.name,
					role: response.role,
					profile_image_url: response.profile_image_url,
					token: response.token
				});
				
				verificationStatus = 'success';
				userEmail = response.email;
				
				// 延迟跳转到主页
				setTimeout(() => {
					goto('/');
				}, 3000);
			}
		} catch (error: any) {
			console.error('Email verification failed:', error);
			verificationStatus = 'error';
			
			if (error.detail?.includes('expired') || error.detail?.includes('过期')) {
				verificationStatus = 'expired';
				errorMessage = '验证链接已过期，请重新发送验证邮件';
			} else if (error.detail?.includes('Invalid') || error.detail?.includes('无效')) {
				errorMessage = '无效的验证链接';
			} else {
				errorMessage = error.detail || '验证失败，请重试';
			}
		}
	}

	async function handleResendVerification() {
		if (!userEmail && !canResend) return;
		
		try {
			isResending = true;
			
			const email = userEmail || prompt('请输入你的邮箱地址：');
			if (!email) {
				isResending = false;
				return;
			}
			
			await resendVerificationEmail(email);
			
			toast.success('验证邮件已重新发送，请检查你的邮箱', {
				duration: 5000
			});
			
			// 设置重发冷却时间
			canResend = false;
			resendCountdown = 60;
			
			const countdown = setInterval(() => {
				resendCountdown--;
				if (resendCountdown <= 0) {
					canResend = true;
					clearInterval(countdown);
				}
			}, 1000);
			
		} catch (error: any) {
			console.error('Resend verification failed:', error);
			toast.error(error.detail || '重发验证邮件失败');
		} finally {
			isResending = false;
		}
	}

	function goToSignIn() {
		goto('/auth');
	}
</script>

<svelte:head>
	<title>邮箱验证 - Econometrics Agent</title>
</svelte:head>

<div class="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50 flex items-center justify-center p-4">
	<div class="max-w-md w-full">
		<div class="bg-white rounded-2xl shadow-xl p-8 text-center">
			<!-- Logo 区域 -->
			<div class="mb-8">
				<div class="text-4xl mb-2">🏛️</div>
				<h1 class="text-2xl font-bold text-gray-900">Econometrics Agent</h1>
				<p class="text-gray-600 text-sm">专业经济计量分析平台</p>
			</div>

			<!-- 验证状态显示 -->
			{#if verificationStatus === 'verifying'}
				<div class="mb-6">
					<div class="animate-spin rounded-full h-16 w-16 border-b-2 border-blue-600 mx-auto mb-4"></div>
					<h2 class="text-xl font-semibold text-gray-900 mb-2">正在验证邮箱...</h2>
					<p class="text-gray-600">请稍候，我们正在验证你的邮箱地址</p>
				</div>
			{:else if verificationStatus === 'success'}
				<div class="mb-6">
					<div class="text-6xl text-green-500 mb-4">✅</div>
					<h2 class="text-xl font-semibold text-green-800 mb-2">邮箱验证成功！</h2>
					<p class="text-gray-600 mb-4">
						恭喜！你的邮箱 <span class="font-medium text-green-700">{userEmail}</span> 已验证成功
					</p>
					<div class="bg-green-50 border border-green-200 rounded-lg p-4 mb-4">
						<p class="text-green-800 text-sm">
							🎉 验证完成！即将自动跳转到主页...
						</p>
					</div>
				</div>
			{:else if verificationStatus === 'expired'}
				<div class="mb-6">
					<div class="text-6xl text-orange-500 mb-4">⏰</div>
					<h2 class="text-xl font-semibold text-orange-800 mb-2">验证链接已过期</h2>
					<p class="text-gray-600 mb-4">{errorMessage}</p>
					<div class="bg-orange-50 border border-orange-200 rounded-lg p-4 mb-4">
						<p class="text-orange-800 text-sm">
							💡 验证链接有效期为24小时，请重新发送验证邮件
						</p>
					</div>
				</div>
			{:else if verificationStatus === 'error'}
				<div class="mb-6">
					<div class="text-6xl text-red-500 mb-4">❌</div>
					<h2 class="text-xl font-semibold text-red-800 mb-2">验证失败</h2>
					<p class="text-gray-600 mb-4">{errorMessage}</p>
					<div class="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
						<p class="text-red-800 text-sm">
							🔧 如果问题持续存在，请联系技术支持
						</p>
					</div>
				</div>
			{/if}

			<!-- 操作按钮区域 -->
			<div class="space-y-3">
				{#if verificationStatus === 'success'}
					<button
						on:click={() => goto('/')}
						class="w-full bg-gradient-to-r from-blue-600 to-purple-600 text-white font-semibold py-3 px-6 rounded-lg hover:from-blue-700 hover:to-purple-700 transition-all duration-200 transform hover:scale-105"
					>
						🚀 立即开始使用
					</button>
				{:else if verificationStatus === 'expired' || verificationStatus === 'error'}
					<button
						on:click={handleResendVerification}
						disabled={isResending || !canResend}
						class="w-full bg-gradient-to-r from-orange-500 to-red-500 text-white font-semibold py-3 px-6 rounded-lg hover:from-orange-600 hover:to-red-600 transition-all duration-200 transform hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none"
					>
						{#if isResending}
							📤 正在发送...
						{:else if !canResend}
							⏳ 请等待 {resendCountdown} 秒
						{:else}
							📧 重新发送验证邮件
						{/if}
					</button>
				{/if}

				<!-- 返回登录按钮 -->
				{#if verificationStatus !== 'verifying'}
					<button
						on:click={goToSignIn}
						class="w-full bg-gray-100 text-gray-700 font-medium py-3 px-6 rounded-lg hover:bg-gray-200 transition-colors duration-200"
					>
						← 返回登录页面
					</button>
				{/if}
			</div>

			<!-- 帮助信息 -->
			{#if verificationStatus !== 'success'}
				<div class="mt-8 pt-6 border-t border-gray-200">
					<h3 class="text-sm font-medium text-gray-900 mb-3">常见问题</h3>
					<div class="text-xs text-gray-600 space-y-2">
						<div class="flex items-start gap-2">
							<span class="text-blue-500">•</span>
							<span>验证邮件可能在垃圾邮件文件夹中</span>
						</div>
						<div class="flex items-start gap-2">
							<span class="text-blue-500">•</span>
							<span>验证链接有效期为24小时</span>
						</div>
						<div class="flex items-start gap-2">
							<span class="text-blue-500">•</span>
							<span>每个验证链接只能使用一次</span>
						</div>
					</div>
				</div>
			{/if}
		</div>
	</div>
</div>

<style>
	@keyframes fadeIn {
		from { opacity: 0; transform: translateY(20px); }
		to { opacity: 1; transform: translateY(0); }
	}
	
	.bg-white {
		animation: fadeIn 0.6s ease-out;
	}
</style>