"""One project-scoped projection for every reusable content-producing feature."""
def sql():
    branches=[
        "SELECT 'file' kind,f.id,f.name title,f.name||' '||COALESCE((SELECT group_concat(p.text||' '||p.notes,' ') FROM pages p WHERE p.file_id=f.id AND p.review_state!='not_applicable'),'') search_text,f.created_at stamp,'' section_id FROM files f WHERE f.project_id=? AND f.active=1",
        "SELECT 'summary',m.file_id,f.name||' · 总结',m.data,m.created_at,'' FROM material_summaries m JOIN files f ON f.id=m.file_id WHERE f.project_id=? AND f.active=1",
        "SELECT 'reference',id,COALESCE(json_extract(metadata,'$.title'),'文献书目'),metadata,created_at,'' FROM bibliography WHERE project_id=? AND COALESCE(json_extract(metadata,'$._studio_deleted'),0)=0",
        "SELECT kind,id,COALESCE(NULLIF(json_extract(data,'$.title'),''),substr(json_extract(data,'$.quote'),1,60),substr(json_extract(data,'$.text'),1,60),kind),data,updated_at,CASE WHEN kind IN ('proposal','comment') THEN owner_id ELSE COALESCE(json_extract(data,'$.section_id'),'') END FROM studio_records WHERE project_id=? AND deleted_at IS NULL AND kind IN ('note','translation','annotation','guide','message','webpage','canvas','parse_result','proposal','search','comment') AND (kind!='message' OR json_extract(data,'$.role')='assistant')",
        "SELECT 'asset',id,name,name||' '||metadata,created_at,COALESCE(json_extract(metadata,'$.section_id'),'') FROM artifacts WHERE project_id=? AND COALESCE(json_extract(metadata,'$.studio_deleted'),0)=0",
        "SELECT 'fact',id,label,label||' '||statement,updated_at,'' FROM facts WHERE project_id=?",
        "SELECT 'analysis',id,'统计分析 · '||COALESCE(json_extract(parameters,'$.method'),''),parameters||' '||result,created_at,'' FROM advanced_runs WHERE project_id=? AND kind='advanced_statistics'",
        "SELECT 'wiki_page',p.id,p.title,p.title||' '||COALESCE((SELECT group_concat(text,' ') FROM wiki_blocks WHERE version_id=p.current_version_id),''),v.created_at,'' FROM wiki_pages p JOIN wiki_versions v ON v.id=p.current_version_id WHERE p.project_id=? AND v.state='published'",
        "SELECT 'assertion',a.claim_version_id,'图谱论断 · '||a.predicate,c.proposition_json,a.created_at,'' FROM kg_assertions a JOIN claim_versions c ON c.id=a.claim_version_id WHERE a.project_id=? AND a.lifecycle NOT IN ('excluded','superseded')",
        "SELECT 'revision',r.id,s.title,r.text,r.created_at,s.id FROM sections s JOIN revisions r ON r.id=s.current_revision_id WHERE s.project_id=? AND s.state!='archived'",
        "SELECT 'rule',id,'论文规则 · '||substr(statement,1,60),statement,created_at,'' FROM rules WHERE project_id=? AND state!='rejected'",
        "SELECT 'rule_package',id,'已发布规则包 v'||version,rules,created_at,'' FROM rule_packages WHERE project_id=?",
        "SELECT 'outline',id,title||' · 章节计划',title||' '||goal,(SELECT updated_at FROM projects WHERE id=sections.project_id),id FROM sections WHERE project_id=? AND state!='archived'",
        "SELECT 'finding',id,'检查意见 · '||substr(message,1,60),message||' '||evidence,created_at,COALESCE(section_id,'') FROM findings WHERE project_id=?",
        "SELECT 'mentor_task',id,'导师意见 · '||substr(comment,1,60),comment||' '||resolution,created_at,COALESCE(section_id,'') FROM mentor_tasks WHERE project_id=?",
        "SELECT 'reading',id,'问题阅读 · '||substr(question,1,60),question||' '||review_note,updated_at,COALESCE(section_id,'') FROM document_readings WHERE project_id=?",
        "SELECT 'evidence',id,'证据 · '||kind||' · '||origin_type,kind||' '||origin_id,created_at,'' FROM evidence_items WHERE project_id=? AND validity NOT IN ('excluded','stale')",
        "SELECT 'source',s.id,f.name||' · 原文片段',s.text,f.created_at,'' FROM sources s JOIN files f ON f.id=s.file_id WHERE f.project_id=? AND f.active=1",
        "SELECT 'unit',u.id,f.name||' · 结构化原文 '||u.ordinal,u.text,p.created_at,'' FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id JOIN files f ON f.id=p.file_id WHERE p.project_id=? AND f.active=1 AND u.review_state!='excluded'",
        "SELECT 'export',id,'交付记录 · '||filename,manifest,created_at,'' FROM exports WHERE project_id=? AND status='ready'",
        "SELECT 'execution',id,'执行结果 · '||kind,COALESCE(result,''),updated_at,'' FROM tasks WHERE project_id=? AND status='succeeded' AND (kind IN ('advanced_execute','advanced_screenshot') OR kind='studio' AND json_extract(payload,'$.action')='execute')",
    ]
    return ' UNION ALL '.join(branches),len(branches)
