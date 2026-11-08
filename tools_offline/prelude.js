// KubeJS가 주는 전역 이름들의 빈 대역. 등록 콜백은 실행하지 않는다(표를 만드는 최상위 코드만 필요하다).
var global = {};
var console = { info: function(){}, log: function(){}, warn: function(){}, error: function(){} };
function __any() { return __anyObj; }
var __anyObj = {};
var StartupEvents = { registry: __any, init: __any, postInit: __any, modifyCreativeTab: __any };
var ForgeEvents = { onEvent: __any }; var ForgeModEvents = { onEvent: __any };
var ItemEvents = { modification: __any, tooltip: __any }; var BlockEvents = { modification: __any };
var EntityJSEvents = { modifyEntity: __any, attributes: __any, addGoals: __any, addGoalSelectors: __any, biomeSpawns: __any, spawnPlacement: __any };
var MoreJSEvents = { registerPotionBrewing: __any }; var ClientEvents = { lang: __any };
var Platform = { isLoaded: function(){ return true; }, isClientEnvironment: function(){ return false; } };
var Java = { loadClass: function(){ return __anyObj; } };
var Item = { of: function(id){ return { id: String(id) }; }, getItem: function(id){ return { id: String(id) }; } };
var Ingredient = { of: function(id){ return { id: String(id) }; } };
var Utils = {}; var JsonIO = { read: function(){ return null; }, write: function(){} };
var Text = { of: function(t){ return t; }, translate: function(t){ return t; }, gray: function(t){ return t; } };
var NBT = {}; var Block = {}; var Fluid = {};
