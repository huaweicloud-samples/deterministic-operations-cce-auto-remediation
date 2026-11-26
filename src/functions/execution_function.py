"""
执行函数 —— FunctionGraph 入口

由审批函数异步调用，读取已批准的修复命令，配置 CCE kubeconfig，
按 立即→预防→备选 顺序执行 kubectl 命令，并将结果回写 OBS。

安全控制：
- 命令白名单（仅 kubectl）
- 危险 shell 字符拒绝
- shlex 安全解析（不使用 shell=True）
- YAML 清单写入临时文件而非管道
"""

import os
import sys
import json
import time
import shlex
import subprocess
import logging
import re

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cce_kubeconfig import get_cce_kubeconfig, write_kubeconfig  # noqa: E402
from approval_request_handler import update_approval_request  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 命令白名单 —— 仅允许 kubectl
ALLOWED_COMMANDS = {'kubectl'}

# 危险 shell 字符/模式（防止 AI 输出注入）
DANGEROUS_PATTERNS = ['&&', '||', ';', '|', '`', '$(', '>', '<']

KUBECONFIG_PATH = '/tmp/kubeconfig'
REGION = os.environ.get('HW_REGION', 'cn-north-4')
CLUSTER_ID = os.environ.get('CLUSTER_ID', '')


def validate_command(command_text):
    """执行前校验命令。返回 (True, None) 或 (False, 原因)。"""
    if not command_text or command_text.strip() == '' or command_text == 'No command found':
        return False, '未提供有效命令'

    base_command = command_text.split('<<')[0] if '<<' in command_text else command_text

    for pattern in DANGEROUS_PATTERNS:
        if pattern in base_command:
            logger.warning(f"拒绝命令 — 含禁用模式 '{pattern}': {command_text[:80]}")
            return False, f'命令含禁用模式: {pattern}'

    try:
        parts = shlex.split(base_command)
    except ValueError as e:
        return False, f'命令解析失败: {e}'

    if not parts:
        return False, '解析后为空命令'

    if parts[0] not in ALLOWED_COMMANDS:
        logger.warning(f"拒绝命令 — 二进制不在白名单: {parts[0]}")
        return False, f'命令不被允许: {parts[0]}。允许: {", ".join(sorted(ALLOWED_COMMANDS))}'

    return True, None


def setup_kubectl(cluster_id, region):
    """通过 CCE SDK 获取 kubeconfig 并写入本地文件"""
    try:
        if not cluster_id:
            cluster_id = CLUSTER_ID
        if not cluster_id:
            logger.error("未提供 CCE 集群 ID")
            return False

        logger.info(f"获取集群 {cluster_id} 的 kubeconfig...")
        kubeconfig_text = get_cce_kubeconfig(cluster_id, region=region)
        write_kubeconfig(kubeconfig_text, KUBECONFIG_PATH)
        logger.info("kubeconfig 写入成功")
        return True
    except Exception as e:
        logger.error(f"kubectl 配置失败: {str(e)}")
        return False


def execute_command(command_text, cluster_id, region):
    """安全执行单条 kubectl 命令"""
    is_valid, rejection_reason = validate_command(command_text)
    if not is_valid:
        return {'success': False, 'output': rejection_reason, 'command': command_text}

    try:
        if 'kubectl apply -f - <<EOF' in command_text:
            return _execute_kubectl_apply_yaml(command_text)

        args = shlex.split(command_text)
        if args[0] == 'kubectl' and '--kubeconfig' not in args:
            args.insert(1, '--kubeconfig')
            args.insert(2, KUBECONFIG_PATH)

        logger.info(f"执行: {' '.join(args)}")
        result = subprocess.run(args, capture_output=True, text=True, timeout=300)

        success = result.returncode == 0
        output = result.stdout if success else result.stderr
        logger.info(f"命令{'成功' if success else '失败'}: {output}")

        return {
            'success': success,
            'output': output,
            'return_code': result.returncode,
            'command': ' '.join(args),
        }
    except subprocess.TimeoutExpired:
        logger.error("命令执行超时")
        return {'success': False, 'output': '命令执行超时', 'command': command_text}
    except ValueError as e:
        logger.error(f"命令解析错误: {str(e)}")
        return {'success': False, 'output': f'命令解析错误: {str(e)}', 'command': command_text}
    except Exception as e:
        logger.error(f"执行错误: {str(e)}")
        return {'success': False, 'output': f'执行错误: {str(e)}', 'command': command_text}


def _execute_kubectl_apply_yaml(command_text):
    """处理带 heredoc YAML 的 kubectl apply（写入临时文件而非管道）"""
    eof_match = re.search(r'kubectl apply -f - <<EOF\s*(.*?)\s*EOF', command_text, re.DOTALL)
    if not eof_match:
        return {'success': False, 'output': '无法解析 EOF 块', 'command': command_text}

    yaml_content = eof_match.group(1).strip()
    logger.info(f"YAML 内容长度: {len(yaml_content)} 字符")

    yaml_file = '/tmp/kubectl-manifest.yaml'
    try:
        with open(yaml_file, 'w') as f:
            f.write(yaml_content)

        args = ['kubectl', '--kubeconfig', KUBECONFIG_PATH, 'apply', '-f', yaml_file]
        logger.info(f"执行: {' '.join(args)}")

        result = subprocess.run(args, capture_output=True, text=True, timeout=300)
        success = result.returncode == 0
        output = result.stdout if success else result.stderr
        logger.info(f"命令{'成功' if success else '失败'}: {output}")

        return {
            'success': success,
            'output': output,
            'return_code': result.returncode,
            'command': ' '.join(args),
        }
    except subprocess.TimeoutExpired:
        logger.error("kubectl apply 超时")
        return {'success': False, 'output': '命令执行超时', 'command': command_text}
    except Exception as e:
        logger.error(f"kubectl apply 错误: {str(e)}")
        return {'success': False, 'output': f'执行错误: {str(e)}', 'command': command_text}
    finally:
        try:
            os.remove(yaml_file)
        except OSError:
            pass


def process_approval_request(approval_request):
    """处理已批准请求并执行命令"""
    try:
        request_id = approval_request.get('request_id', 'unknown')
        cluster_id = approval_request.get('cluster_name', CLUSTER_ID)
        region = REGION

        immediate = approval_request.get('immediate_action_command', 'No command found')
        preventive = approval_request.get('preventive_action_command', 'No command found')
        alternative = approval_request.get('alternative_action_command', 'No command found')

        logger.info(f"🚀 执行已批准动作 请求: {request_id}")
        logger.info(f"📊 集群: {cluster_id}")

        if not setup_kubectl(cluster_id, region):
            update_approval_request(request_id, status='failed', execution_results={'error': 'kubeconfig 配置失败'})
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'kubeconfig 配置失败', 'request_id': request_id}),
            }

        actions = [
            ('immediate', immediate),
            ('preventive', preventive),
            ('alternative', alternative),
        ]

        results = []
        overall_success = False

        for action_type, action_command in actions:
            logger.info(f"🔄 处理 {action_type} 动作: {action_command}")
            result = execute_command(action_command, cluster_id, region)
            result['action_type'] = action_type
            results.append(result)

            if result['success']:
                logger.info(f"✅ {action_type} 动作成功 - 停止执行")
                overall_success = True
                results.append({
                    'action_type': 'resolution',
                    'message': f'{action_type} 动作已解决问题',
                    'resolved_by': action_type,
                })
                break
            else:
                logger.warning(f"❌ {action_type} 动作失败 - 尝试下一个")

        execution_results = {
            'success': overall_success,
            'results': results,
            'total_actions_attempted': len([r for r in results if 'command' in r]),
        }

        final_status = 'completed' if overall_success else 'failed'
        update_approval_request(request_id, status=final_status, execution_results=execution_results)

        logger.info(f"🏁 执行完成 - 成功: {overall_success}")
        return {
            'statusCode': 200 if overall_success else 500,
            'body': json.dumps({
                'message': '执行完成',
                'request_id': request_id,
                'cluster_name': cluster_id,
                'success': overall_success,
                'results': results,
            }),
        }
    except Exception as e:
        logger.error(f"❌ 处理失败: {str(e)}")
        rid = approval_request.get('request_id', 'unknown') if isinstance(approval_request, dict) else 'unknown'
        update_approval_request(rid, status='failed', execution_results={'error': str(e)})
        return {
            'statusCode': 500,
            'body': json.dumps({'error': '处理失败', 'message': str(e), 'request_id': rid}),
        }


def handler(event, context):
    """执行函数入口，接收已批准的审批记录（由审批函数异步传入）"""
    try:
        logger.info(f"📥 收到执行请求: {json.dumps(event, default=str)[:1000]}")

        if 'body' in event:
            approval_request = event['body']
            if isinstance(approval_request, str):
                approval_request = json.loads(approval_request)
        else:
            approval_request = event

        return process_approval_request(approval_request)

    except Exception as e:
        logger.error(f"❌ 执行函数异常: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': '执行函数失败', 'message': str(e)})}
