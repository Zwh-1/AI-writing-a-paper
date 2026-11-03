// Locate pages through the visible navigation, supporting the new grouped workspaces.
const groups={
 '资料中心':'文献资料与规则','论文规则':'文献资料与规则','文献与知识':'文献资料与规则',
 '提纲与计划':'提纲与写作','写作工作台':'提纲与写作','有据写作':'提纲与写作',
 '研究与成果':'研究与协作','研究助手':'研究与协作','产物与互通':'研究与协作','高级研究与协作':'研究与协作',
 '修改与检查':'检查与交付','排版与交付':'检查与交付'
};
async function openPage(page,title){
 const original=page.getByRole('button',{name:title,exact:true});
 if(await original.count()){await original.first().click();return;}
 const group=groups[title];if(!group)throw new Error('Unknown workspace: '+title);
 await page.getByRole('navigation',{name:'项目功能导航',exact:true}).getByRole('button',{name:group,exact:true}).click();
 const navigation=page.getByRole('navigation',{name:group+'工作区',exact:true});
 await navigation.getByRole('button',{name:new RegExp('^'+title)}).click();
}
module.exports={openPage};
