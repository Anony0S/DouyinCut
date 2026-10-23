// Load the production TypeScript modules with API mocks; Vue Router and Pinia stay real.
const fs=require('node:fs')
const path=require('node:path')
const ts=require('typescript')
const {parse,compileScript}=require('vue/compiler-sfc')

exports.modules=function(overrides={}) {
  const cache=new Map(),root=path.resolve(__dirname,'../src')
  function load(file) {
    file=path.resolve(file)
    if(overrides[file]) return overrides[file]
    if(cache.has(file)) return cache.get(file).exports
    if(file.endsWith('.vue')) return {__esModule:true,default:{render:()=>null}}
    const result={exports:{}};cache.set(file,result)
    const source=fs.readFileSync(file,'utf8')
    const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
    new Function('require','module','exports',code)(name=>{
      if(!name.startsWith('.')) return require(name)
      const dependency=path.resolve(path.dirname(file),name)
      return load(path.extname(dependency) ? dependency : `${dependency}.ts`)
    },result,result.exports)
    return result.exports
  }
  return {load:relative=>load(path.resolve(root,relative)),root}
}

exports.componentScript=function(relative,resolve) {
  const source=fs.readFileSync(path.resolve(__dirname,'../src',relative),'utf8')
  const compiled=compileScript(parse(source).descriptor,{id:'component-test'})
  const code=ts.transpileModule(compiled.content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
  const result={exports:{}}
  new Function('require','module','exports',code)(resolve,result,result.exports)
  return result.exports.default
}
