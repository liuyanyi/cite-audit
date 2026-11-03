import * as Tabs from '@radix-ui/react-tabs';
import { providerNames } from '../format';
import { sourceDescriptions } from '../sourceDescriptions';
import { AlertTriangle, ArrowUpRight, CircleCheck, CircleHelp, CircleMinus, CircleX, Clock3, LoaderCircle } from 'lucide-react';

const statuses = [
  { label: '版本已确认', color: '绿色', tone: 'success', icon: CircleCheck, description: '已确认当前引用的论文身份和出版版本。预印本也可以是有效的已确认版本。' },
  { label: '找到新版本', color: '橙色', tone: 'conflict', icon: ArrowUpRight, description: '已有可靠的版本升级建议，例如找到正式发表版。下方另行说明当前版本是否已确认；不会自动替换引用。' },
  { label: '关键字段须修改', color: '红色', tone: 'danger', icon: CircleX, description: '当前出版版本已确认，且证据明确指出关键字段有误。详情页用红色“须修改”标出对应字段。' },
  { label: '存在字段冲突', color: '琥珀色', tone: 'warning', icon: AlertTriangle, description: '输入字段与核验依据存在冲突，请进入详情查看具体字段和证据。版本已确认与字段冲突可以同时成立。' },
  { label: '未找到匹配', color: '红色', tone: 'danger', icon: CircleX, description: '已查询的来源没有返回匹配记录。请求预算耗尽和来源访问失败单独统计。' },
  { label: '无法核验', color: '灰色', tone: 'muted', icon: CircleHelp, description: '证据不足、身份有歧义，或仅确认身份但尚未确认当前版本。具体原因显示在下方及详情中。' },
  { label: '不支持核验', color: '灰色', tone: 'muted', icon: CircleMinus, description: '当前核验能力不支持该条目，例如专利。不会据此认定引用有误。' },
  { label: '检查失败', color: '红色', tone: 'danger', icon: CircleX, description: '该条目处理失败，例如发生解析或执行错误。查看错误详情后可重新核查。' },
  { label: '排队中', color: '灰色', tone: 'muted', icon: Clock3, description: '等待开始处理，尚无本轮核验结论。' },
  { label: '核查中', color: '灰色', tone: 'muted', icon: LoaderCircle, description: '正在检索和核验，结果尚未定稿。' },
  { label: '已取消', color: '灰色', tone: 'muted', icon: CircleMinus, description: '该条目的处理已取消，不表示已完成核验。任务中已经完成的其他结果保留。' },
] as const;

export default function HelpContent() {
  return <Tabs.Root defaultValue="start" className="help-tabs">
    <Tabs.List className="tabs" aria-label="使用说明主题">
      <Tabs.Trigger value="start">快速开始</Tabs.Trigger>
      <Tabs.Trigger value="logic">核验逻辑</Tabs.Trigger>
      <Tabs.Trigger value="sources">数据源</Tabs.Trigger>
      <Tabs.Trigger value="results">读懂结果</Tabs.Trigger>
    </Tabs.List>
    <Tabs.Content value="start" className="help-content">
    <section aria-labelledby="help-workflow"><h3 id="help-workflow">从一份 Bib 开始</h3><ol className="help-steps">
      <li><strong>上传完整文件。</strong>每次上传都是独立任务。演示模式使用隔离的离线样例，不代表在线核实。</li>
      <li><strong>先核验当前版本。</strong>字段冲突与正式版建议分别显示。准确的预印本不会仅因存在正式版而判错。</li>
      <li><strong>从判断追溯到证据。</strong>点击字段依据定位来源；点击来源字段返回对照表。</li>
      <li><strong>预览后导出新文件。</strong>只应用你明确选择的建议。未决条目保留，上传原件不修改。</li>
    </ol></section>
    </Tabs.Content>
    <Tabs.Content value="logic" className="help-content">
      <section><h3>怎样确认一条引用？</h3><ol className="help-steps">
        <li><strong>定位候选。</strong>有 DOI、arXiv 编号或官方论文链接时，可按标识查询。只有题录时，可按题名搜索，或通过会场和年份定位论文集。</li>
        <li><strong>核对论文身份。</strong>系统比较规范化后的题名与作者，排除题名明显不符的候选。题名相近但作者不一致时，需要进一步的身份依据。</li>
        <li><strong>区分出版版本。</strong>系统结合标识符、出版来源和官方题录判断输入指向预印本、会议版还是期刊版。只能确认论文身份时，报告会提示版本仍待核实。</li>
        <li><strong>逐字段对照。</strong>确认版本后，用该版本的来源记录核对输入。例如，arXiv 记录指向次年发表的会议版时，会议年份属于推荐版本差异，预印本仍按其自身记录核对。</li>
      </ol></section>
    </Tabs.Content>
    <Tabs.Content value="sources" className="help-content">
      <p>在设置中配置并保存要启用的数据源。每条引用根据已有标识、题名和出版信息使用适用的查询方式。打开条目详情，可查看本次取得的来源证据。</p>
      {Object.entries(sourceDescriptions).map(([id, paragraphs]) => <details key={id}>
        <summary>{id === 'cnki' ? '中国知网（CNKI）' : providerNames[id] || id}</summary>
        {paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
      </details>)}
    </Tabs.Content>
    <Tabs.Content value="results" className="help-content">
    <section aria-labelledby="help-statuses"><h3 id="help-statuses">条目列表状态</h3><dl className="help-status-list">
      {statuses.map(({ label, color, tone, icon: Icon, description }) => <div key={label}>
        <dt style={{ color: `var(--${tone})` }}><Icon size={17} aria-hidden="true" /><strong>{label}</strong><span>{color}</span></dt>
        <dd>{description}</dd>
      </div>)}
    </dl></section>
    <section aria-labelledby="help-critical"><h3 id="help-critical">哪些是关键字段？</h3>
      <ul><li>标题 <code>title</code>、作者 <code>author</code>、出版年份 <code>year</code></li>
        <li>DOI <code>doi</code>、预印本编号 <code>eprint</code></li>
        <li>期刊名称 <code>journal</code> 或 <code>journaltitle</code>、论文集或收录书名 <code>booktitle</code></li></ul>
      <p>缺少字段可提示补全。预印本与推荐正式版之间的年份或刊载差异，在“推荐版本差异”中查看。</p>
    </section>
    <section aria-labelledby="help-status-rules"><h3 id="help-status-rules">如何理解组合状态</h3>
      <p>同时存在普通字段冲突和新版本时，优先显示“存在字段冲突”，并附橙色“找到新版本”提示。当前版本的核验结论保留在补充说明中。</p>
      <p>不支持核验或尚未正常完成的条目，优先显示对应的支持情况或执行状态。核验进度可多选，例如同时查看排队中和核验中的条目。核验结论、字段与版本和关键词可叠加；“字段冲突”包含关键字段须修改的条目，“找到新版本”可独立选择。“清除筛选”恢复全部条目。</p>
      <p>演示条目始终标注“演示数据”，其中“版本已确认”使用灰色。所有演示结果均为离线样例，不能作为在线证据。</p>
    </section>
    </Tabs.Content>
  </Tabs.Root>;
}
