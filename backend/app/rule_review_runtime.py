"""Bounded DeepSeek request settings and recovery of known response failures."""
import re
from . import provider,summary_billing

MAX_CONTENT_ATTEMPTS=2


def output_limit(configuration,count):
    return min(int(configuration['max_output_tokens']),max(1024,896*count))


def request_config(configuration,limit,primary=True):
    value={**configuration,'max_output_tokens':min(int(configuration['max_output_tokens']),limit)}
    if primary:
        # Rule review needs the final JSON, even when general chat uses thinking.
        value['thinking_enabled']=False
        value.pop('reasoning_effort',None)
        # Only explicit rate-limit rejections are retried by tasks._model_call.
        value['max_attempts']=3
    return value


def failure_kind(error):
    if isinstance(error,summary_billing.BudgetExceeded):return 'budget'
    if isinstance(error,provider.ProviderError) and error.unknown:return 'outcome_unknown'
    partial=getattr(error,'response_result',None)
    if partial is not None:
        usage=partial.get('usage',{}) or {};raw=usage.get('raw',{}) or {}
        reasoning=(raw.get('completion_tokens_details') or {}).get('reasoning_tokens')
        if not partial.get('text') and reasoning and reasoning==usage.get('output_tokens'):return 'reasoning_exhausted'
        return 'invalid_output'
    if getattr(error,'retryable',False):return 'rate_limited'
    message=str(error)
    if re.search(r'上下文|context.{0,20}(?:limit|length|exceed)',message,re.I):return 'context_limit'
    if isinstance(error,ValueError) or re.search(r'JSON|rules 列表|decisions 列表',message,re.I):return 'invalid_output'
    return 'provider_rejected'


def smaller(items):
    middle=max(1,len(items)//2)
    return [items[:middle],items[middle:]] if len(items)>1 else [items]


PAUSE_MESSAGES={
    'reasoning_exhausted':'DeepSeek 连续只返回推理用量，没有可用审核正文；已在本次请求关闭推理，接口仍未返回正文，已暂停以避免继续消耗费用。',
    'outcome_unknown':'连续两次远端执行结果未知，已保存完成结果并暂停新请求；未知请求没有自动重发。',
    'provider_rejected':'接口明确拒绝审核请求，已保存完成结果；请按任务中的具体错误检查接口权限或配置。',
    'rate_limited':'接口持续限流，有限次数等待重试后仍未恢复；已保存进度。',
}
