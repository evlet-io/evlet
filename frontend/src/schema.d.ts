import type {FieldSchema, ModelSchema, RecordData} from './runtime/types';
export const fieldTypes:Set<string>;
export function validateSchema(schema:any):ModelSchema[];
export function visibleFields(model:ModelSchema):FieldSchema[];
export const idField:FieldSchema;
export function fieldFor(model:ModelSchema,name:string):FieldSchema;
export function recordLabel(model:ModelSchema,record:RecordData):string;
export function formatValue(field:FieldSchema,value:any,labels?:Record<string,string>):string;
export function inputValue(field:FieldSchema,value:any):any;
