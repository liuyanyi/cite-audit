import { AlertTriangle, ArrowUpRight, CircleCheck, CircleHelp, CircleMinus, CircleX, Clock3, LoaderCircle } from 'lucide-react';

const statuses = [
  { label: '版本已确认', color: '绿色', tone: 'success', icon: CircleCheck, description: '已确认当前引用的论文身份和出版版本。预印本也可以是有效的已确认版本。' },
  { label: '找到新版本', color: '橙色', tone: 'conflict', icon: ArrowUpRight, description: '已有可靠的版本升级建议，例如找到正式发表版。下方另行说明当前版本是否已确认；不会自动替换引用。' },
  { label: '存在字段冲突', color: '琥珀色', tone: 'warning', icon: AlertTriangle, description: '输入字段与核验依据存在冲突，请进入详情查看具体字段和证据。版本已确认与字段冲突可以同时成立。' },
  { label: '未找到匹配', color: '红色', tone: 'danger', icon: CircleX, description: '本次未能找到匹配记录。目前也包含来源查询失败的情况，请查看调用记录区分原因；不代表论文不存在。' },
  { label: '无法核验', color: '灰色', tone: 'muted', icon: CircleHelp, description: '证据不足、身份有歧义，或仅确认身份但尚未确认当前版本。具体原因显示在下方及详情中。' },
  { label: '不支持核验', color: '灰色', tone: 'muted', icon: CircleMinus, description: '当前核验能力不支持该条目，例如专利。不会据此认定引用有误。' },
  { label: '检查失败', color: '红色', tone: 'danger', icon: CircleX, description: '该条目处理失败，例如发生解析或执行错误。查看错误详情后可重新核查。' },
  { label: '排队中', color: '灰色', tone: 'muted', icon: Clock3, description: '等待开始处理，尚无本轮核验结论。' },
  { label: '核查中', color: '灰色', tone: 'muted', icon: LoaderCircle, description: '正在检索和核验，结果尚未定稿。' },
  { label: '已取消', color: '灰色', tone: 'muted', icon: CircleMinus, description: '该条目的处理已取消，不表示已完成核验。任务中已经完成的其他结果保留。' },
] as const;

export default function HelpContent() {
  return <div className="modal-body help-content">
    <section aria-labelledby="help-workflow"><h3 id="help-workflow">从一份 Bib 开始</h3><ol className="help-steps">
      <li><strong>上传完整文件。</strong>每次上传都是独立任务。演示模式使用隔离的离线样例，不代表在线核实。</li>
      <li><strong>先核验当前版本。</strong>字段冲突与正式版建议分别显示。准确的预印本不会仅因存在正式版而判错。</li>
      <li><strong>从判断追溯到证据。</strong>点击字段依据定位来源；点击来源字段返回对照表。</li>
      <li><strong>预览后导出新文件。</strong>只应用你明确选择的建议。未决条目保留，上传原件不修改。</li>
    </ol></section>
    <section aria-labelledby="help-statuses"><h3 id="help-statuses">条目列表状态</h3><dl className="help-status-list">
      {statuses.map(({ label, color, tone, icon: Icon, description }) => <div key={label}>
        <dt style={{ color: `var(--${tone})` }}><Icon size={17} aria-hidden="true" /><strong>{label}</strong><span>{color}</span></dt>
        <dd>{description}</dd>
      </div>)}
    </dl></section>
    <section aria-labelledby="help-status-rules"><h3 id="help-status-rules">如何理解组合状态</h3>
      <p>同时存在字段冲突和新版本时，优先显示“存在字段冲突”，并附橙色“找到新版本”提示。当前版本的核验结论保留在补充说明中。</p>
      <p>不支持核验或尚未正常完成的条目，优先显示对应的支持情况或执行状态。三个类型筛选按当前版本核验分类；“找到新版本”和“字段冲突”可单独开关，也可与类型、关键词组合筛选。多个条件同时开启时，只显示全部条件均符合的条目；“重置”清除所有筛选。</p>
      <p>演示条目始终标注“演示数据”，其中“版本已确认”使用灰色。所有演示结果均为离线样例，不能作为在线证据。</p>
    </section>
  </div>;
}
