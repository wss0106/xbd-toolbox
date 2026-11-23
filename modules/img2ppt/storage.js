/* 图片转 PPT · 存储适配层
   页面只调这些接口，底层落到本地文件包 API。 */
(function(global){
  "use strict";

  function req(url, options){
    return fetch(url, options).then(function(r){ return r.json(); });
  }
  function post(path, body){
    return req(path, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body||{})
    });
  }

  var Storage = {
    list: function(){
      return req("../../api/projects").then(function(d){
        return d.ok ? d.projects : [];
      });
    },
    create: function(name, settings){
      return post("../../api/project/create", {name:name, settings:settings||{}})
        .then(function(d){ return d.ok ? d.project : Promise.reject(new Error(d.error)); });
    },
    open: function(id){
      return req("../../api/project/open?id="+encodeURIComponent(id)).then(function(d){
        return d.ok ? d.project : Promise.reject(new Error(d.error||"打开失败"));
      });
    },
    save: function(id, name, settings, images){
      return post("../../api/project/save", {id:id, name:name, settings:settings, images:images})
        .then(function(d){
          if(!d.ok) throw new Error(d.error||"保存失败");
          return d;
        });
    },
    rename: function(id, name){
      return post("../../api/project/rename", {id:id, name:name})
        .then(function(d){ if(!d.ok) throw new Error(d.error||"重命名失败"); return d; });
    },
    remove: function(id){
      return post("../../api/project/delete", {id:id})
        .then(function(d){ if(!d.ok) throw new Error(d.error||"删除失败"); return d; });
    },
    saveExport: function(id, filename, blob){
      return new Promise(function(res, rej){
        var fr = new FileReader();
        fr.onload = function(){
          post("../../api/project/export-ppt", {id:id, filename:filename, dataUrl:fr.result})
            .then(function(d){ d.ok ? res(d) : rej(new Error(d.error)); })
            .catch(rej);
        };
        fr.onerror = rej;
        fr.readAsDataURL(blob);
      });
    },
    exportZip: function(id){
      location.href = "../../api/project/export?id="+encodeURIComponent(id);
    },
    reveal: function(id){
      return post("../../api/export/reveal", {id:id})
        .then(function(d){ if(!d.ok) throw new Error(d.error||"打开失败"); return d; });
    },
    getTemplates: function(){
      return req("../../api/templates").then(function(d){
        return d.ok ? d.data : {version:1,templates:[]};
      });
    },
    saveTemplates: function(data){
      return post("../../api/templates/save", {data:data})
        .then(function(d){ if(!d.ok) throw new Error(d.error||"保存模板失败"); return d; });
    },
    getFetchRules: function(){
      return req("../../api/fetch-rules").then(function(d){
        return d.ok ? d.data : {version:1,rules:[]};
      });
    },
    saveFetchRules: function(data){
      return post("../../api/fetch-rules/save", {data:data})
        .then(function(d){ if(!d.ok) throw new Error(d.error||"保存规则失败"); return d; });
    },
    importZip: function(file){
      return new Promise(function(res, rej){
        var fr = new FileReader();
        fr.onload = function(){
          post("../../api/project/import", {dataUrl:fr.result})
            .then(function(d){ d.ok ? res(d.project) : rej(new Error(d.error)); })
            .catch(rej);
        };
        fr.onerror = rej;
        fr.readAsDataURL(file);
      });
    }
  };

  global.ProjectStorage = Storage;
})(window);
