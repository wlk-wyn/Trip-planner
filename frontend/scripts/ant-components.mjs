import { AntDesignVueResolver } from 'unplugin-vue-components/resolvers'

// Resolve this app's components through their public per-component ES entry.
// A shared barrel otherwise connects eager and lazy routes' component graphs.
export const basic = ['BackTop','Button','Card','Col','ConfigProvider','DatePicker','Descriptions',
    'Divider','Dropdown','Empty','Form','Input','InputNumber','Layout','List','Menu','Modal',
    'Row','Select','Space','Spin','Tag','Upload','Checkbox']
export const groups = {
    BackTop:'float-button',
    CheckboxGroup:'checkbox', DescriptionsItem:'descriptions', FormItem:'form', InputSearch:'input', Textarea:'input',
    LayoutHeader:'layout', LayoutContent:'layout', LayoutFooter:'layout', ListItem:'list', ListItemMeta:'list',
    MenuItem:'menu', MenuDivider:'menu', SelectOption:'select',
  }
export function directAntComponents() {
  const fallback = AntDesignVueResolver({importStyle:false})
  return {type:'component', resolve(name) {
    const original = fallback.resolve(name)
    if (!original || !name.startsWith('A')) return original
    const component = name.slice(1)
    if (groups[component]) return {name:component, from:`ant-design-vue/es/${groups[component]}`}
    if (basic.includes(component)) return {name:'default',from:`ant-design-vue/es/${component.replace(/([a-z])([A-Z])/g,'$1-$2').toLowerCase()}`}
    return original
  }}
}
